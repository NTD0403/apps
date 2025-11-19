from flask import Blueprint, render_template, request, flash, jsonify, redirect, url_for
from flask_login import login_user, login_required, logout_user, current_user
from .models import Note, User, PlayerState, GameLog, GameRoom, Notification, GameChat
from . import db
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import math
import numpy as np
import io
import base64
from datetime import datetime, timezone, timedelta
import random
from sqlalchemy.orm import joinedload

views = Blueprint('views', __name__)

SEAWATER_LOCATIONS = {
    '1a1', '2a1', '3a1', '4a1', '1b1', '2b1',
      '3b1', '4b1', '1c1', '2c1', '3c1', '4c1', '1d1', '2d1', '3d1', '4d1',
      '1e1', '2e1', '1f1', '2f1', '3f1', '1g1', '2g1', '3g1', '4g1', '1h1',
      '2h1', '3h1', '4h1', '1i1', '2i1', '3i1', '4i1', '1j1', '2j1', '3j1',
      '4j1', '1a2', '3a2', '4a2', '1b2', '2b2', '3b2', '4b2', '1c2', '2c2',
      '4c2', '1g2', '2g2', '2h2', '1i2', '2i2', '1j2', '2j2', '3j2', '4j2',
      '1a3', '3a3', '4a3', '1j3', '2j3', '3j3', '4j3', '1a4', '4a4', '1j4', '2j4',
      '3j4', '4j4', '1a5', '4a5', '1j5', '2j5', '3j5', '4j5', '1a6', '2a6',
      '3a6', '4a6', '1j6', '2j6', '3j6', '4j6', '1a7', '2a7', '3a7', '4a7',
      '1j7', '2j7', '3j7', '4j7', '1a8', '2a8', '3a8', '4a8', '1j8', '2j8',
      '3j8', '4j8', '1a9', '2a9', '3a9', '4a9', '3b9', '1c9', '2c9', '3c9',
      '4c9', '4g9', '2j9', '3j9', '1a10', '2a10', '3a10', '4a10', '1b10',
      '2b10', '3b10', '4b10', '2e10', '3e10', '4e10', '1f10', '2f10', '3f10',
      '4f10', '1g10', '2g10', '3g10', '4g10', '1h10', '2h10', '3h10', '4h10',
      '3i10', '4i10', '2j10', '3j10', '4j10'
}

FRESH_WATER_LOCATIONS = {
    '3c3', '4c3', '1c4', '2c4', '3d4', '4d4', '1d5', '2d5', '3d5', '4d5', '1d6', '2d6', '1e6'
}

JUNGLE_SQUARES = {'c6', 'h4', 'e8', 'i8'}

HERBS_LOCATIONS_POOL = [
    '1e2', '1h2', '2c3', '4e3', '2g3', '2i3', '1b5', '4b6', '1c6', '4e6', '4f4', '2h4',
    '2h6', '2i6', '4i6', '2c7', '2e7', '1c8', '2b9', '2d8', '4f8', '2g8', '2i8', '2d9',
    '1c10', '4h9', '3i9', '2c5', '2b4', '4f2'
]


HERB_SPAWN_CONFIG = {
    'tuong_tu': 2,
    'thuong_quan': 2,
    'phan_thien': 2,
    'quynh_tam': 1,
    'ly_sau': 4,
    'nhat_nguyet': 2,
    'u_tam': 4
}

def apply_penalty(player_state):
    player_state.current_water = 1.0
    player_state.stun_expires_at = datetime.now(timezone.utc) + timedelta(hours=6)

def resolve_spirit_combat(spirit_a, spirit_b):
    rules = {
        'Dragon': 'Tiger',
        'Tiger': 'Bird',
        'Bird': 'Tortoise',
        'Tortoise': 'Dragon'
    }

    if rules.get(spirit_a) == spirit_b:
        return 'WIN'
    elif rules.get(spirit_b) == spirit_a:
        return 'LOSE'
    else:
        return 'DRAW'

def check_and_trigger_traps(victim_state, current_room_id):
    enemy_seekers = PlayerState.query.filter(
        PlayerState.room_id == current_room_id,
        PlayerState.team != victim_state.team,
        PlayerState.role == 'Seeker'
    ).all()
    hit_trap = False
    for enemy in enemy_seekers:
        if enemy.active_trap_location == victim_state.current_location:
            if enemy.active_trap_time:
                trap_time = enemy.active_trap_time
                if trap_time.tzinfo is None:
                    trap_time = trap_time.replace(tzinfo=timezone.utc)
                time_diff = datetime.now(timezone.utc) - trap_time
                if time_diff.total_seconds() < 48 * 3600:
                    hit_trap = True

                    victim_state.current_water -= 3.0

                    enemy.active_trap_location = None
                    enemy.active_trap_time = None
                    db.session.add(enemy)


                    log_msg = f"Seeker '{victim_state.user.first_name}' ({victim_state.team}) have fallen into the trap of '{enemy.user.first_name}' in '{victim_state.current_location}' and lose 3.0 water bars!"
                    new_log = GameLog(log_message=log_msg, user_id=victim_state.user_id, room_id=current_room_id, privacy='public')
                    db.session.add(new_log)
                else:
                    enemy.active_trap_location = None
                    enemy.active_trap_time = None
                    db.session.add(enemy)
    return hit_trap


def check_if_main_square_is_coastal(main_square):

    if not main_square or len(main_square) < 2 or len(main_square) > 3:
        return False


    if f"1{main_square}" in SEAWATER_LOCATIONS:
        return True
    if f"2{main_square}" in SEAWATER_LOCATIONS:
        return True
    if f"3{main_square}" in SEAWATER_LOCATIONS:
        return True
    if f"4{main_square}" in SEAWATER_LOCATIONS:
        return True

    return False


def parse_coordinate_safe(coord_str):

    try:

        p_char = coord_str[0]
        x_char = coord_str[1]
        y_str = coord_str[2:]


        x = ord(x_char) - ord('a') + 1
        y = int(y_str)


        if p_char not in '1234' or not (1 <= x <= 10) or not (1 <= y <= 10):
            return None

    except (IndexError, ValueError, TypeError):

        return None


    if p_char == '1':
        X = x - 0.75
        Y = y - 0.75
    elif p_char == '2':
        X = x - 0.25
        Y = y - 0.75
    elif p_char == '3':
        X = x - 0.25
        Y = y - 0.25
    else:
        X = x - 0.75
        Y = y - 0.25


    return (X, Y, x, y)


def get_super_square(main_square):
    try:
        x_char = main_square[0]
        y_val = int(main_square[1:])
        x_ord = ord(x_char)

        super_square_zones = set()


        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:

                new_x_ord = x_ord + dx
                new_y_val = y_val + dy


                if (ord('a') <= new_x_ord <= ord('j')) and (1 <= new_y_val <= 10):


                    new_x_char = chr(new_x_ord)
                    new_main_square = f"{new_x_char}{new_y_val}"
                    super_square_zones.add(new_main_square)

        return super_square_zones

    except Exception as e:
        print(f"Error in '{main_square}': {e}")
        return set()



def time_calculator_main(l, k):
    if (len(l) != 3 and len(l) != 4) or (len(l) == 4 and l[3] != '0') or (len(l) == 3 and l[2] not in '123456789'):
        return "Invalid coordinate"
    x1 = ord(l[1]) - ord('a') + 1
    if x1 > 10 or x1 < 1:
        return "Invalid coordinate"
    if len(l) == 4:
        y1 = 10
    elif len(l) == 3 and (l[2] in '123456789'):
        y1 = int(l[2])
    if (len(k) != 3 and len(k) != 4) or (len(k) == 4 and k[3] != '0') or (len(k) == 3 and k[2] not in '123456789'):
        return "Invalid coordinate"
    x2 = ord(k[1]) - ord('a') + 1
    if x2 > 10 or x2 < 1:
        return "Invalid coordinate"
    if len(k) == 4:
        y2 = 10
    elif len(k) == 3 and (k[2] in '123456789'):
        y2 = int(k[2])
    if l[0] == '1':
        X1 = x1 - 0.75
        Y1 = y1 - 0.75
    elif l[0] == '2':
        X1 = x1 - 0.25
        Y1 = y1 - 0.75
    elif l[0] == '3':
        X1 = x1 - 0.25
        Y1 = y1 - 0.25
    elif l[0] == '4':
        X1 = x1 - 0.75
        Y1 = y1 - 0.25
    else:
        return "Invalid coordinate"
    if k[0] == '1':
        X2 = x2 - 0.75
        Y2 = y2 - 0.75
    elif k[0] == '2':
        X2 = x2 - 0.25
        Y2 = y2 - 0.75
    elif k[0] == '3':
        X2 = x2 - 0.25
        Y2 = y2 - 0.25
    elif k[0] == '4':
        X2 = x2 - 0.75
        Y2 = y2 - 0.25
    else:
        return "Invalid coordinate"
    d_in = ((X2-X1)**2 + (Y2-Y1)**2)**(1/2)
    d_hard = ((9.75-0.25)**2 + (9.75-0.25)**2)**(1/2)
    t_hard = 21600
    t_out = t_hard * (d_in/d_hard)

    return {
        "t_out": t_out,
        "X1": X1, "Y1": Y1,
        "X2": X2, "Y2": Y2
    }


def violence_detector_main(l, k, m):
    if (len(l) != 3 and len(l) != 4) or (len(l) == 4 and l[3] != '0') or (len(l) == 3 and l[2] not in '123456789'):
        return "Invalid coordinate"
    x1 = ord(l[1]) - ord('a') + 1
    if x1 > 10 or x1 < 1:
        return "Invalid coordinate"
    if len(l) == 4:
        y1 = 10
    elif len(l) == 3 and (l[2] in '123456789'):
        y1 = int(l[2])
    if (len(k) != 3 and len(k) != 4) or (len(k) == 4 and k[3] != '0') or (len(k) == 3 and k[2] not in '123456789'):
        return "Invalid coordinate"
    x2 = ord(k[1]) - ord('a') + 1
    if x2 > 10 or x2 < 1:
        return "Invalid coordinate"
    if len(k) == 4:
        y2 = 10
    elif len(k) == 3 and (k[2] in '123456789'):
        y2 = int(k[2])
    if (len(m) != 3 and len(m) != 4) or (len(m) == 4 and m[3] != '0') or (len(m) == 3 and m[2] not in '123456789'):
        return "Invalid coordinate"
    x3 = ord(m[1]) - ord('a') + 1
    if x3 > 10 or x3 < 1:
        return "Invalid coordinate"
    if len(m) == 4:
        y3 = 10
    elif len(m) == 3 and (m[2] in '123456789'):
        y3 = int(m[2])
    if l[0] == '1':
        X1 = x1 - 0.75
        Y1 = y1 - 0.75
    elif l[0] == '2':
        X1 = x1 - 0.25
        Y1 = y1 - 0.75
    elif l[0] == '3':
        X1 = x1 - 0.25
        Y1 = y1 - 0.25
    elif l[0] == '4':
        X1 = x1 - 0.75
        Y1 = y1 - 0.25
    else:
        return "Invalid coordinate"
    if k[0] == '1':
        X2 = x2 - 0.75
        Y2 = y2 - 0.75
    elif k[0] == '2':
        X2 = x2 - 0.25
        Y2 = y2 - 0.75
    elif k[0] == '3':
        X2 = x2 - 0.25
        Y2 = y2 - 0.25
    elif k[0] == '4':
        X2 = x2 - 0.75
        Y2 = y2 - 0.25
    else:
        return "Invalid coordinate"
    if m[0] == '1':
        X3 = x3 - 0.75
        Y3 = y3 - 0.75
    elif m[0] == '2':
        X3 = x3 - 0.25
        Y3 = y3 - 0.75
    elif m[0] == '3':
        X3 = x3 - 0.25
        Y3 = y3 - 0.25
    elif m[0] == '4':
        X3 = x3 - 0.75
        Y3 = y3 - 0.25
    else:
        return "Invalid coordinate"
    x11 = x3 - 1
    y11 = y3 - 1
    x12 = x3
    y12 = y3 - 1
    x21 = x3 - 1
    y21 = y3
    x22 = x3
    y22 = y3
    a = (Y2 - Y1)*(x11 - X1) - (X2 - X1)*(y11 - Y1)
    b = (Y2 - Y1)*(x12 - X1) - (X2 - X1)*(y12 - Y1)
    c = (Y2 - Y1)*(x21 - X1) - (X2 - X1)*(y21 - Y1)
    d = (Y2 - Y1)*(x22 - X1) - (X2 - X1)*(y22 - Y1)
    if X1 < X2:
        X_low_limit = x1 - 1
        X_high_limit = x2
    else:
        X_low_limit = x2 - 1
        X_high_limit = x1
    if Y1 > Y2:
        Y_low_limit = y1
        Y_high_limit = y2 - 1
    else:
        Y_low_limit = y2
        Y_high_limit = y1 - 1
    if (a > 0 and b > 0 and c > 0 and d > 0) or (a < 0 and b < 0 and c < 0 and d < 0) or X3 < X_low_limit or X3 > X_high_limit or Y3 > Y_low_limit or Y3 < Y_high_limit:
        result_text = "Violence is not executed"
    else:
        result_text = "Violence occurs"

    return {
        "result": result_text,
        "X1": X1, "Y1": Y1,
        "X2": X2, "Y2": Y2,
        "X3": X3, "Y3": Y3
    }


def check_main_square_intersection(l, k, main_square):

    try:
        if (len(l) != 3 and len(l) != 4) or (len(l) == 4 and l[3] != '0') or (len(l) == 3 and l[2] not in '123456789'): return False
        x1 = ord(l[1]) - ord('a') + 1
        if x1 > 10 or x1 < 1: return False
        y1 = 10 if len(l) == 4 else int(l[2])
        if (len(k) != 3 and len(k) != 4) or (len(k) == 4 and k[3] != '0') or (len(k) == 3 and k[2] not in '123456789'): return False
        x2 = ord(k[1]) - ord('a') + 1
        if x2 > 10 or x2 < 1: return False
        y2 = 10 if len(k) == 4 else int(k[2])
        if l[0] == '1': X1, Y1 = x1 - 0.75, y1 - 0.75
        elif l[0] == '2': X1, Y1 = x1 - 0.25, y1 - 0.75
        elif l[0] == '3': X1, Y1 = x1 - 0.25, y1 - 0.25
        elif l[0] == '4': X1, Y1 = x1 - 0.75, y1 - 0.25
        else: return False
        if k[0] == '1': X2, Y2 = x2 - 0.75, y2 - 0.75
        elif k[0] == '2': X2, Y2 = x2 - 0.25, y2 - 0.75
        elif k[0] == '3': X2, Y2 = x2 - 0.25, y2 - 0.25
        elif k[0] == '4': X2, Y2 = x2 - 0.75, y2 - 0.25
        else: return False

        if not (2 <= len(main_square) <= 3): return False
        x3 = ord(main_square[0]) - ord('a') + 1
        y3 = int(main_square[1:])
        if not (1 <= x3 <= 10) or not (1 <= y3 <= 10): return False

        x11, y11 = x3 - 1, y3 - 1
        x12, y12 = x3,     y3 - 1
        x21, y21 = x3 - 1, y3
        x22, y22 = x3,     y3

        a = (Y2 - Y1)*(x11 - X1) - (X2 - X1)*(y11 - Y1)
        b = (Y2 - Y1)*(x12 - X1) - (X2 - X1)*(y12 - Y1)
        c = (Y2 - Y1)*(x21 - X1) - (X2 - X1)*(y21 - Y1)
        d = (Y2 - Y1)*(x22 - X1) - (X2 - X1)*(y22 - Y1)
        if X1 < X2:
            X_low_limit = x1 - 1
            X_high_limit = x2
        else:
            X_low_limit = x2 - 1
            X_high_limit = x1
        if Y1 > Y2:
            Y_low_limit = y1
            Y_high_limit = y2 - 1
        else:
            Y_low_limit = y2
            Y_high_limit = y1 - 1
        if (a > 0 and b > 0 and c > 0 and d > 0) or (a < 0 and b < 0 and c < 0 and d < 0) or (x3 - 0.5) < X_low_limit or (x3 - 0.5) > X_high_limit or (y3 - 0.5) > Y_low_limit or (y3 - 0.5) < Y_high_limit:
            return False
        else:
            return True
    except Exception as e:
        print(f"Error caused by creating jungle-squares: {e}")
        return False




@views.route('/', methods=['GET', 'POST'])
@login_required
def home():
     return render_template("home.html", user=current_user)

@views.route('/add_note', methods=['GET', 'POST'])
@login_required
def add_note():
     if request.method == 'POST':
          note = request.form.get('note')

          if not note or len(note) < 1:
               flash("Note is too short", category='error')
          else:
               new_note = Note(data=note, user_id=current_user.id)
               db.session.add(new_note)
               db.session.commit()
               flash("Note added!", category='success')

     return render_template("add_note.html", user=current_user)

@views.route('/delete-note', methods=['POST'])
@login_required
def delete_note():
     note = json.loads(request.data)
     noteId = note['noteId']
     note = Note.query.get(noteId)
     if note:
          if note.user_id == current_user.id:
               db.session.delete(note)
               db.session.commit()
     return jsonify({})


def generate_plot_base64(l, k, plot_data):

    X1 = plot_data['X1']
    Y1 = plot_data['Y1']
    X2 = plot_data['X2']
    Y2 = plot_data['Y2']
    t_out = plot_data['t_out']


    fig, ax = plt.subplots(figsize=(6, 6))

    ax.set_xlim(0, 10)
    ax.set_ylim(10, 0)
    ax.set_xticks(range(10))
    ax.set_yticks(range(10))
    ax.xaxis.set_ticks_position('top')
    ax.xaxis.set_label_position('top')
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    for i, label in enumerate(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']):
        ax.text(i + 0.5, -0.5, label, ha='center', va='top')
    for i, label in enumerate(['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']):
        ax.text(-0.3, i + 0.5, label, ha='right', va='center')
    ax.plot(X1, Y1, 'ro')
    ax.text(X1 + 0.2, Y1 - 0.2, l, color='red')
    ax.plot(X2, Y2, 'ro')
    ax.text(X2 + 0.2, Y2 - 0.2, k, color='red')
    ax.annotate('', xy = (X2, Y2), xytext = (X1, Y1), arrowprops = dict(arrowstyle = '->', color = 'green', linewidth = 2))

    if t_out <= 3600:
        ax.text(5, 10.8, f"The expected time: {int(t_out)//60}m", color='blue', ha = 'center', va = 'bottom')
    elif t_out > 3600:
        ax.text(5, 10.8, f"The expected time: {int(t_out)//3600}h {(int(t_out)%3600)//60}m", color='blue', ha = 'center', va = 'bottom')

    plt.grid(True, linestyle='--')


    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)


    data = base64.b64encode(buf.getbuffer()).decode("ascii")

    return data


def generate_violence_plot_base64(l, k, m, plot_data):

    X1 = plot_data['X1']
    Y1 = plot_data['Y1']
    X2 = plot_data['X2']
    Y2 = plot_data['Y2']
    X3 = plot_data['X3']
    Y3 = plot_data['Y3']
    result_text = plot_data['result']

    fig, ax = plt.subplots(figsize=(6, 6))

    ax.set_xlim(0, 10)
    ax.set_ylim(10, 0)
    ax.set_xticks(range(10))
    ax.set_yticks(range(10))
    ax.xaxis.set_ticks_position('top')
    ax.xaxis.set_label_position('top')
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    for i, label in enumerate(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']):
        ax.text(i + 0.5, -0.5, label, ha='center', va='top')
    for i, label in enumerate(['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']):
        ax.text(-0.3, i + 0.5, label, ha='right', va='center')


    ax.plot(X1, Y1, 'ro')
    ax.text(X1 + 0.2, Y1 - 0.2, l, color='red')
    ax.plot(X2, Y2, 'ro')
    ax.text(X2 + 0.2, Y2 - 0.2, k, color='red')


    ax.plot(X3, Y3, 'bo')
    ax.text(X3 + 0.2, Y3 - 0.2, m, color='blue')


    ax.annotate('', xy = (X2, Y2), xytext = (X1, Y1), arrowprops = dict(arrowstyle = '->', color = 'green', linewidth = 2))


    color = 'red' if result_text == "Violence occurs" else 'green'
    ax.text(5, 10.8, result_text, color=color, ha = 'center', va = 'bottom', fontsize=12, weight='bold')

    plt.grid(True, linestyle='--')


    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)

    data = base64.b64encode(buf.getbuffer()).decode("ascii")
    return data


def generate_game_map_plot(current_player_state, teammates, enemies, is_detecting, beast_locations=None, is_god_view=False, room_herb_mapping=None):

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_xlim(0, 10)
    ax.set_ylim(10, 0)
    ax.set_xticks(range(10))
    ax.set_yticks(range(10))
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    for i, label in enumerate(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']):
        ax.text(i + 0.5, -0.5, label, ha='center', va='top')
    for i, label in enumerate(['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']):
        ax.text(-0.3, i + 0.5, label, ha='right', va='center')
    plt.grid(True, linestyle='--')

    if (is_god_view or current_player_state.has_ly_sau_thao) and beast_locations:
        for beast_loc in beast_locations:
            if beast_loc:
                try:
                    x_char, y_str = beast_loc[0], beast_loc[1:]
                    x = ord(x_char) - ord('a') + 0.5
                    y = int(y_str) - 0.5
                    ax.plot(x, y, 'rx', markersize=35, markeredgewidth=5, alpha=0.4, zorder=1)
                except: pass

    if is_god_view and room_herb_mapping:
        try:
            mapping = json.loads(room_herb_mapping)
            for loc, herb_type in mapping.items():
                coords = parse_coordinate_safe(loc)
                if coords:
                    X, Y, _, _ = coords
                    ax.plot(X, Y, '.', color='#FFD700', markersize=5, zorder=2)

        except: pass



    if is_god_view:

        for player in teammates:
            if player.role == 'Gamemaster': continue

            coords = parse_coordinate_safe(player.current_location)
            if coords:
                X, Y, _, _ = coords


                color = 'red' if player.team == 'TeamA' else 'blue'


                marker = 's' if player.role == 'Seeker' else 'o'

                ax.plot(X, Y, marker, color=color, markersize=10, alpha=0.8, zorder=5)
                ax.text(X + 0.2, Y - 0.2, player.user.first_name, color=color, fontsize=8, weight='bold', zorder=6)

    else:

        for teammate in teammates:
            if teammate.user_id == current_player_state.user_id: continue
            tm_coords = parse_coordinate_safe(teammate.current_location)
            if tm_coords:
                TM_X, TM_Y, _, _ = tm_coords
                ax.plot(TM_X, TM_Y, 'o', color='blue', markersize=8, alpha=0.7)
                ax.text(TM_X + 0.2, TM_Y - 0.2, teammate.user.first_name, color='blue', fontsize=8)


        if is_detecting:
            for enemy in enemies:
                if enemy.has_nhat_nguyet_thao: continue
                enemy_coords = parse_coordinate_safe(enemy.current_location)
                if enemy_coords:
                    EN_X, EN_Y, _, _ = enemy_coords
                    ax.plot(EN_X, EN_Y, 'o', color='#FFA500', markersize=8, alpha=0.7)


        coords = parse_coordinate_safe(current_player_state.current_location)
        if coords:
            X, Y, _, _ = coords
            color = 'green' if current_player_state.role == 'Hider' else 'red'
            ax.plot(X, Y, 'o', color=color, markersize=12, zorder=5, markeredgecolor='black')
            ax.text(X + 0.2, Y - 0.2, current_player_state.current_location, color=color, weight='bold', zorder=6)


    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)
    data = base64.b64encode(buf.getbuffer()).decode("ascii")
    return data



@views.route('/time_calculator', methods=['GET', 'POST'])
@login_required
def time_calculator():

    output = None
    plot_image = None
    start_coordinate = ""
    end_coordinate = ""

    if request.method == 'POST':

        start_coordinate = request.form.get('start_coordinate')
        end_coordinate = request.form.get('end_coordinate')
        output = time_calculator_main(start_coordinate, end_coordinate)
        if type(output) == str:
            flash("INVALID COORDINATE!", category="error")
        else:
            plot_image = generate_plot_base64(start_coordinate, end_coordinate, output)

            return render_template('time_calculator_result.html', user=current_user, output=output, start_coordinate=start_coordinate, end_coordinate=end_coordinate, plot_image=plot_image)
    return render_template('time_calculator.html', user=current_user, output=output, start_coordinate=start_coordinate, end_coordinate=end_coordinate)


@views.route('/violence_detector', methods=['GET', 'POST'])
@login_required
def violence_detector():

    start_coordinate_of_player_1 = ""
    end_coordinate_of_player_1 = ""
    location_of_player_2 = ""
    plot_image = None

    if request.method == 'POST':

        start_coordinate_of_player_1 = request.form.get('start_coordinate_of_player_1')
        end_coordinate_of_player_1 = request.form.get('end_coordinate_of_player_1')
        location_of_player_2 = request.form.get('location_of_player_2')

        result_data = violence_detector_main(start_coordinate_of_player_1, end_coordinate_of_player_1, location_of_player_2)
        if type(result_data) == str:
            flash("INVALID COORDINATE!", category="error")
        else:
            output = result_data['result']
            plot_image = generate_violence_plot_base64(start_coordinate_of_player_1, end_coordinate_of_player_1, location_of_player_2, result_data)

            return render_template('violence_detector_result.html', user=current_user, start_coordinate_of_player_1=start_coordinate_of_player_1, end_coordinate_of_player_1 = end_coordinate_of_player_1, location_of_player_2 = location_of_player_2, output=output, plot_image=plot_image)
    return render_template('violence_detector.html', user=current_user, start_coordinate_of_player_1=start_coordinate_of_player_1, end_coordinate_of_player_1 = end_coordinate_of_player_1, location_of_player_2 = location_of_player_2)

@views.route('/about_se_3_eng')
@login_required
def about_se_3_eng():
        return render_template('about_se_3_eng.html', user=current_user)


@views.route('/about_se_3_vie')
@login_required
def about_se_3_vie():
        return render_template('about_se_3_vie.html', user=current_user)

@views.route('/linear')
@login_required
def linear():
        return render_template('linear.html', user=current_user)





@views.route('/rooms', methods=['GET', 'POST'])
@login_required
def game_rooms():
    if request.method == 'POST':
        room_name = request.form.get('room_name')
        mode = request.form.get('mode')
        if not room_name:
            flash('Please enter a room name.', category='error')
        else:
            new_room = GameRoom(room_name=room_name, host_id=current_user.id, mode=mode)
            try:
                beast_squares = random.sample(list(JUNGLE_SQUARES), 2)
                new_room.beast_square_1 = beast_squares[0]
                new_room.beast_square_2 = beast_squares[1]
                print(f"{new_room.room_name} created jungle-squared in: '{beast_squares}'")
            except Exception as e:
                print(f"Error caused by creating jungle-squares: {e}")

            db.session.add(new_room)
            db.session.commit()

            flash(f'Room "{room_name}" created. Now choose your role.', category='success')
            return redirect(url_for('views.game_lobby', room_id=new_room.id))

    if current_user.player_state:

        return redirect(url_for('views.game_lobby'))



    rooms_to_display = []

    try:

        player_count_subq = db.session.query(
            PlayerState.room_id,
            db.func.count(PlayerState.id).label('player_count')
        ).group_by(PlayerState.room_id).subquery()


        rooms_query = GameRoom.query.options(
            joinedload(GameRoom.host)
        ).join(
            player_count_subq,
            GameRoom.id == player_count_subq.c.room_id,
            isouter=True
        ).filter(
            GameRoom.status == 'waiting'
        ).add_columns(
            db.func.coalesce(player_count_subq.c.player_count, 0).label('player_count_val')
        ).order_by(GameRoom.date_created.desc())


        all_waiting_rooms_with_counts = rooms_query.all()


        rooms_cleaned = 0
        for room, player_count in all_waiting_rooms_with_counts:
            if player_count == 0:
                db.session.delete(room)
                rooms_cleaned += 1
            else:

                rooms_to_display.append((room, player_count))

        if rooms_cleaned > 0:
            db.session.commit()
            print(f"[CLEANUP] Cleaned up {rooms_cleaned} empty room(s).")

    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Error fetching/cleaning rooms: {e}")
        flash("Error loading rooms.", "error")


    return render_template('simulate_se_3_rooms.html',
                           user=current_user,
                           rooms_with_counts=rooms_to_display)





@views.route('/game_lobby', methods=['GET', 'POST'])
@login_required
def game_lobby():

    state = current_user.player_state
    if state:

        return redirect(url_for('views.game_dashboard'))


    room_id = request.args.get('room_id')
    if not room_id:
        flash("You must join a room first.", "error")
        return redirect(url_for('views.game_rooms'))

    room = GameRoom.query.get(room_id)
    if not room or room.status != 'waiting':
        flash("This room is invalid or already in progress.", "error")
        return redirect(url_for('views.game_rooms'))

    if room.mode == 'competition' and current_user.id == room.host_id:
        if request.method == 'GET':
             pass

        state = PlayerState(
            user_id=current_user.id,
            team='God',
            role='Gamemaster',
            current_location='0a0',
            room_id=room.id,
            current_water=999.0
        )
        db.session.add(state)
        db.session.commit()
        return redirect(url_for('views.game_dashboard'))

    if request.method == 'POST':
        team = request.form.get('team')
        role_from_form = request.form.get('role')
        if role_from_form == 'Hider':

            existing_hider = PlayerState.query.filter_by(
                room_id=room.id,
                team=team,
                role='Hider'
            ).first()

            if existing_hider:
                flash(f"{team} had a hider already. Please select Seeker.", "error")
                return render_template('simulate_se_3_lobby.html', user=current_user, room=room)
        role = role_from_form
        location = None
        spirit_class = None

        if role == 'Random':
            role = random.choice(['Hider', 'Seeker'])
            while location is None or location in SEAWATER_LOCATIONS:
                p = random.choice(['1', '2', '3', '4'])
                c = random.choice(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j'])
                r = random.choice(['1', '2', '3', '4', '5', '6', '7', '8', '9', '10'])
                location = f"{p}{c}{r}"
            if role == 'Seeker':
                spirit_class = random.choice(['Dragon', 'Tiger', 'Bird', 'Tortoise'])
        else:
            location = request.form.get('start_location')

            if not location or parse_coordinate_safe(location) is None or location in SEAWATER_LOCATIONS:
                flash("Invalid or seawater coordinate.", "error")
                return render_template('simulate_se_3_lobby.html', user=current_user, room=room)
            if role == 'Seeker':
                spirit_from_form = request.form.get('spirit')
                if spirit_from_form == 'Random':
                    spirit_class = random.choice(['Dragon', 'Tiger', 'Bird', 'Tortoise'])
                else:
                    spirit_class = spirit_from_form
        state = PlayerState(user_id=current_user.id,
                            team=team,
                            role=role,
                            current_location=location,
                            room_id=room.id,
                            spirit_class=spirit_class,
                            last_action_time=datetime.now(timezone.utc),
                            last_active_post_time=datetime.now(timezone.utc)
                            )

        db.session.add(state)
        db.session.commit()

        try:

            log_msg = f"A player named '{current_user.first_name}' joined room {room.id}"
            new_log = GameLog(log_message=log_msg, user_id=current_user.id)
            db.session.add(new_log)
            db.session.commit()
        except Exception as e:
            flash(f"Error caused by logging: {e}", "error")

        flash_msg = f"Joined room {room.id} successfully (Role: {role}) at {location}."
        flash(flash_msg, "success")
        return redirect(url_for('views.game_dashboard'))


    return render_template('simulate_se_3_lobby.html', user=current_user, room=room)


def end_game_and_cleanup_room(room_id, log_message, flash_message):
    try:
        room_to_delete = GameRoom.query.get(room_id)
        if not room_to_delete:
            print(f"Room {room_id} has been terminated already.")
            return

        all_players_in_room = PlayerState.query.filter_by(room_id=room_id).all()
        for player in all_players_in_room:
            db.session.delete(player)

        GameLog.query.filter_by(room_id=room_id).delete()
        GameChat.query.filter_by(room_id=room_id).delete()

        log_user_id = current_user.id if current_user.is_authenticated else None
        new_log = GameLog(log_message=log_message, user_id=log_user_id, room_id=room_id, privacy='public')
        db.session.add(new_log)
        db.session.delete(room_to_delete)
        db.session.commit()
        flash(flash_message, "success_center")
    except Exception as e:
        db.session.rollback()
        flash(f"Extreme error occurs due to cleaning a room up: {e}", "error")
        print(f"[ERROR] Can not clean the room {room_id}: {e}")

def create_game_log(state, log_message, privacy='team'):
    if not state:
        return
    try:
        new_log = GameLog(
            log_message=log_message,
            user_id=state.user_id,
            room_id=state.room_id,
            team_id=state.team,
            privacy=privacy
        )
        db.session.add(new_log)
    except Exception as e:
        flash(f"Error while writing log: {e}", "error")



@views.route('/game_dashboard', methods=['GET', 'POST'])
@login_required
def game_dashboard():
    state = PlayerState.query.with_for_update().filter_by(user_id=current_user.id).first()
    if not state:
        flash("Please select your team and your role first!", "error")
        return redirect(url_for('views.game_rooms'))
    current_room_id = state.room_id


    last_time = state.last_action_time
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)
    last_active_time = state.last_active_post_time
    if last_active_time.tzinfo is None:
        last_active_time = last_active_time.replace(tzinfo=timezone.utc)


    now_utc = datetime.now(timezone.utc)
    vietnam_tz_offset = timedelta(hours=7)
    now_vietnam = now_utc + vietnam_tz_offset
    current_hour_vietnam = now_vietnam.hour
    today_date = now_vietnam.date()

    room = state.room
    if room.daily_herb_spawn_date != today_date:
        available_spots = list(HERBS_LOCATIONS_POOL)
        random.shuffle(available_spots)

        new_mapping = {}

        try:
            for herb_code, count in HERB_SPAWN_CONFIG.items():
                for _ in range(count):
                    if available_spots:
                        spot = available_spots.pop()
                        new_mapping[spot] = herb_code

            room.daily_herb_mapping = json.dumps(new_mapping)
            room.daily_herb_spawn_date = today_date

            db.session.commit()
            print(f"Room {room.id}: Daily herbs spawned successfully for {today_date}.")

        except Exception as e:
            db.session.rollback()
            print(f"Error spawning daily herbs: {e}")


    is_window_active = (20 <= current_hour_vietnam < 22)
    is_tram_tuong_spawned = False

    if is_window_active:

        if room.tram_tuong_herb_day != today_date:
            random_minute = random.randint(0, 119)
            room.tram_tuong_herb_day = today_date
            room.tram_tuong_herb_minute = random_minute

            try:
                db.session.commit()
                print(f"Room {room.id}: 'Trầm Tương' herb rolled. Spawns at 8:00 PM + {random_minute} mins.")
            except Exception as e:
                db.session.rollback()
                print(f"Error saving herb roll: {e}")


        if room.tram_tuong_herb_minute is not None:

            current_minute_in_window = (current_hour_vietnam - 20) * 60 + now_vietnam.minute

            if current_minute_in_window >= room.tram_tuong_herb_minute:
                is_tram_tuong_spawned = True

    if state.role == 'Gamemaster':
        if request.method == 'POST':
            action = request.form.get('action')

            if action == 'restore':
                log_msg = f"GAMEMASTER '{current_user.first_name}' has reset the game room."
                flash_msg = f"Room has been reset by Host."
                end_game_and_cleanup_room(current_room_id, log_msg, flash_msg)
                return redirect(url_for('views.game_rooms'))

            return redirect(url_for('views.game_dashboard'))

        all_players_in_room = PlayerState.query.filter_by(room_id=current_room_id).all()

        beast_locations = [room.beast_square_1, room.beast_square_2]

        plot_image = generate_game_map_plot(
            current_player_state=state,
            teammates=all_players_in_room,
            enemies=[],
            is_detecting=False,
            beast_locations=beast_locations,
            is_god_view=True,
            room_herb_mapping=room.daily_herb_mapping
        )


        return render_template('simulate_se_3.html',
                               user=current_user,
                               state=state,
                               plot_image=plot_image,
                               all_players=all_players_in_room,
                               is_gamemaster=True,
                               players_in_room=[], can_take_water=False,
                               show_transfer_button=False, all_teammates=[],
                               max_transferable_water=0, teammates_at_loc=[],
                               show_teleport_button=False,
                               violence_enabled=room.violence_enabled,
                               show_track_button=False, show_gambit_button=False,
                               show_purify_button=False
                               )





    else:
        thirst_multiplier = 1.0
        if (state.role == 'Seeker' and
            state.current_location == '3g7' and
            is_window_active):

            inactive_time_elapsed = datetime.now(timezone.utc) - last_active_time
            inactive_minutes = inactive_time_elapsed.total_seconds() / 60

            if inactive_minutes > 15:
                thirst_multiplier = 3.0

                flash("You do not feel so good in this location. Be careful!", "info")

        time_elapsed = datetime.now(timezone.utc) - last_time
        hours_elapsed = time_elapsed.total_seconds() / 3600

        water_lost = hours_elapsed * (1.0 / 6.0) * thirst_multiplier
        if water_lost > 0.0001:
            state.current_water -= water_lost
            state.last_action_time = datetime.now(timezone.utc)

            if state.current_water <= 0:
                if state.has_quynh_tam_thao:
                    state.has_quynh_tam_thao = False
                    state.current_water = 2.0
                    flash("You ran out of water, but 'Quỳnh tâm hoán mệnh thảo' saved you! 2.0 water bars is recoveried.", "success")
                    create_game_log(state, f"Player '{current_user.first_name}' ({state.team}) used 'Quỳnh tâm hoán mệnh thảo' to revive.", privacy='public')

                else:
                    state.current_water = 0
                    state.game_status = "Eliminated (Thirst)"

                    if state.role == 'Hider':
                        hider_team = state.team
                        hider_room_id = state.room_id
                        hider_room_name = state.room.room_name
                        hider_name = current_user.first_name
                        hider_user_id = current_user.id

                        log_msg_hider_elim = f"Hider named '{hider_name}' ({hider_team}) ran out of water and was eliminated."
                        new_log = GameLog(log_message=log_msg_hider_elim, user_id=hider_user_id, room_id=hider_room_id, team_id=hider_team, privacy='public')
                        db.session.add(new_log)

                        current_user.score -= 10
                        db.session.add(current_user)

                        db.session.delete(state)


                        new_hider_state = PlayerState.query.filter(
                            PlayerState.room_id == hider_room_id,
                            PlayerState.team == hider_team,
                            PlayerState.game_status == "Active",
                            PlayerState.role == "Seeker"
                        ).order_by(
                            PlayerState.current_water.desc()
                        ).first()


                        if not new_hider_state:

                            log_msg_game_over = f"Team {hider_team} has no Seekers left to become the new Hider. Team {hider_team} loses. Room '{hider_room_name}' is terminated."
                            flash_msg_game_over = f"You were eliminated (Score: -10pt), and your team has no one left to hide. Team {hider_team} loses."

                            db.session.commit()

                            end_game_and_cleanup_room(hider_room_id, log_msg_game_over, flash_msg_game_over)
                            return redirect(url_for('views.game_rooms'))

                        else:

                            new_hider_state.role = "Hider"

                            log_msg_new_hider = f"'{new_hider_state.user.first_name}' ({hider_team}) is the new Hider at {new_hider_state.current_location}."

                            create_game_log(new_hider_state, log_msg_new_hider, privacy='team')
                            db.session.commit()
                            flash(f"You ran out of water and were eliminated! Your score: -10pt. '{new_hider_state.user.first_name}' is now your team's Hider.", "error")
                            return redirect(url_for('views.game_rooms'))

                    else:
                        log_msg = f"Seeker named '{current_user.first_name}' ({state.team}) is terminated due to running out of water {state.current_location}."
                        new_log = GameLog(log_message=log_msg, user_id=current_user.id)
                        db.session.add(new_log)

                        db.session.delete(state)
                        db.session.commit()
                        flash("You are terminated by running out of water!", "error")
                        return redirect(url_for('views.game_rooms'))
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(f"An error occurred saving your water status: {e}", "error")
                return redirect(url_for('views.game_dashboard'))

        if request.method == 'POST':
            action = request.form.get('action')
            log_msg = None

            if action != 'restore' and state.stun_expires_at:
                stun_time = state.stun_expires_at
                if stun_time.tzinfo is None:
                    stun_time = stun_time.replace(tzinfo=timezone.utc)

                if stun_time > datetime.now(timezone.utc):
                    time_left = (stun_time - datetime.now(timezone.utc)).total_seconds() / 3600
                    flash(f"You are stunned due to losing combat! Unable to act for {time_left:.1f}h.", "error")
                    return redirect(url_for('views.game_dashboard'))

            if action == 'move' and state.role == 'Seeker':
                new_loc = request.form.get('new_location')
                current_loc = state.current_location

                if not new_loc or not current_loc:
                    flash("System error. Empty coordinate!", "error")
                elif new_loc in SEAWATER_LOCATIONS:
                    flash(f"Can't move to '{new_loc}' because it is seawater!", "error")
                else:
                    result_data = time_calculator_main(current_loc, new_loc)

                    if isinstance(result_data, str):
                        flash(f"Can't move: {result_data}", "error")
                    else:
                        time_cost_seconds = result_data['t_out']
                        time_cost_hours = time_cost_seconds / 3600
                        water_cost = time_cost_hours * (1.0 / 6.0)
                        water_cost = round(water_cost, 2)

                        if state.current_water - water_cost < 0:
                            if state.has_quynh_tam_thao:
                                state.has_quynh_tam_thao = False
                                state.current_water = 2.0
                                flash("You ran out of water, but 'Quỳnh tâm hoán mệnh thảo' saved you! 2.0 water bars is recoveried.", "success")
                                create_game_log(state, f"Player '{current_user.first_name}' ({state.team}) used 'Quỳnh tâm hoán mệnh thảo' to revive.", privacy='public')

                                if state.current_water - water_cost < 0:

                                    flash("Even with 'Quỳnh tâm hoán mệnh thảo' , your water is not enough for this trip! You are terminated.", "error")

                                    log_msg = f"Seeker named '{current_user.first_name}' ({state.team}) is terminated due to running out of water while trying to move to '{new_loc}'."
                                    create_game_log(state, log_msg, privacy='public')
                                    db.session.delete(state)
                                    db.session.commit()
                                    return redirect(url_for('views.game_rooms'))

                            else:
                                flash("You don't have enough water to move! You are terminated!", "error")
                                log_msg = f"Seeker named '{current_user.first_name}' ({state.team}) is terminated due to running out of water while trying to move to '{new_loc}'."
                                create_game_log(state, log_msg, privacy='public')
                                db.session.delete(state)
                                db.session.commit()
                                return redirect(url_for('views.game_rooms'))

                        state.current_water -= water_cost
                        state.current_location = new_loc
                        state.last_action_time = datetime.now(timezone.utc)
                        state.last_active_post_time = datetime.now(timezone.utc)
                        state.is_detecting = False

                        if check_and_trigger_traps(state, current_room_id):
                            flash("BOOM! You have stepped into the enemy's trap.! Lose 3.0 water bars.", "error")

                            if state.current_water <= 0:
                                if state.has_quynh_tam_thao:
                                    state.has_quynh_tam_thao = False
                                    state.current_water = 5.0
                                    flash("Trap make you run out of water, but 'Quỳnh tâm hoán mệnh thảo' saved you!", "success")
                                    create_game_log(state, f"Player '{current_user.first_name}' used Quỳnh tâm hoán mệnh thảo to survive after stepping on a trap.", privacy='public')
                                else:
                                    state.current_water = 0
                                    state.game_status = "Eliminated (Trap)"
                                    log_msg = f"Seeker '{current_user.first_name}' ({state.team}) is terminated due to step into a trap."
                                    create_game_log(state, log_msg, privacy='public')
                                    db.session.delete(state)
                                    db.session.commit()
                                    return redirect(url_for('views.game_rooms'))

                        flash(f"Moved to '{new_loc}'. Spent {time_cost_hours:.1f} hour(s). Cost {water_cost} water bar(s).", "success")
                        create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) moved from '{current_loc}' to '{new_loc}'.", privacy='team')

                        if state.room.violence_enabled:
                            current_main_sq = state.current_location[1:]

                            all_players_here = PlayerState.query.filter(
                                PlayerState.room_id == current_room_id,
                                PlayerState.role == 'Seeker',
                                PlayerState.game_status == 'Active'
                            ).all()

                            fighters_in_square = []
                            for p in all_players_here:
                                p_main_sq = p.current_location[1:]
                                if p_main_sq == current_main_sq:
                                    fighters_in_square.append(p)

                            my_team_fighters = [p for p in fighters_in_square if p.team == state.team]
                            enemy_fighters = [p for p in fighters_in_square if p.team != state.team]

                            if enemy_fighters:

                                if len(my_team_fighters) > len(enemy_fighters):
                                    for enemy in enemy_fighters:
                                        apply_penalty(enemy)
                                    flash("Combat win! Overwhelming numbers. Enemies are stunned!", "success")
                                    create_game_log(state, f"Combat in '{current_main_sq}': {state.team} (The majority) defeated {enemy_fighters[0].team}.", privacy='public')

                                    state.has_teleport = True

                                elif len(enemy_fighters) > len(my_team_fighters):
                                    for ally in my_team_fighters:
                                        apply_penalty(ally)
                                    enemy_fighters[0].has_teleport = True
                                    flash("Combat lost! There are too many enemies. You are stunned!", "error")
                                    create_game_log(state, f"Combat in '{current_main_sq}': {state.team} is dominated by {enemy_fighters[0].team}.", privacy='public')

                                else:
                                    enemy = enemy_fighters[0]
                                    result = resolve_spirit_combat(state.spirit_class, enemy.spirit_class)

                                    if result == 'WIN':
                                        apply_penalty(enemy)
                                        state.has_teleport = True
                                        flash(f"You won! Your spirit counters the enemy's spirit.", "success")
                                        create_game_log(state, f"Duel in '{current_main_sq}': {state.user.first_name} defeated {enemy.user.first_name}.", privacy='public')

                                    elif result == 'LOSE':
                                        apply_penalty(state)
                                        enemy.has_teleport = True
                                        flash(f"You lost! Your spirit is countered.", "error")
                                        create_game_log(state, f"Duel in '{current_main_sq}': {state.user.first_name} is defeated by {enemy.user.first_name} .", privacy='public')

                                    else:
                                        state.current_water -= 1.0
                                        enemy.current_water -= 1.0
                                        flash("Draw! Two equally matched spirits. Both lost 1.0 water bar.", "info")
                                        create_game_log(state, f"Duel in '{current_main_sq}': {state.user.first_name} and {enemy.user.first_name} draw.", privacy='public')
                                db.session.commit()


                        now_utc = datetime.now(timezone.utc)
                        vietnam_tz_offset = timedelta(hours=7)
                        now_vietnam = now_utc + vietnam_tz_offset
                        current_hour_vietnam = now_vietnam.hour

                        beast_locations = []


                        if not (7 <= current_hour_vietnam < 22):

                            beast_locations = list(JUNGLE_SQUARES)
                            flash("LUNAR ECLIPSE! All four forests are infested with beasts. Be careful.!", "error")
                        else:

                            beast_locations = [state.room.beast_square_1, state.room.beast_square_2]

                        for beast_loc in beast_locations:
                            if beast_loc:

                                if check_main_square_intersection(current_loc, new_loc, beast_loc):


                                    state.current_water -= 1.0


                                    flash(f"You ran through a beast's territory! You lost an extra 1.0 water and your position was revealed.", "error")

                                    log_msg_public = f"Seeker named '{current_user.first_name}' in ({state.team}) encountered a wild beast! His current position is {new_loc}."
                                    create_game_log(state, log_msg_public, privacy='public')

                                    if state.current_water <= 0:

                                        if state.has_quynh_tam_thao:
                                            state.has_quynh_tam_thao = False
                                            state.current_water = 2.0
                                            flash("You were attacked by a beast and ran out of water, but 'Quỳnh tâm hoán mệnh thảo' saved you!", "success")
                                            create_game_log(state, f"Player '{current_user.first_name}' ({state.team}) used 'Quỳnh tâm hoán mệnh thảo' to survive after encountering the beast.", privacy='team')

                                        else:

                                            state.current_water = 0
                                            state.game_status = "Eliminated (Beast)"
                                            log_msg_elim = f"Seeker named '{current_user.first_name}' ({state.team}) was eliminated by a beast while trying to move to '{new_loc}'."
                                            create_game_log(state, log_msg_elim, privacy='public')
                                            db.session.delete(state)
                                            db.session.commit()
                                            flash("You ran out of water after encountering a beast and were eliminated!", "error")
                                            return redirect(url_for('views.game_rooms'))

            elif action == 'search' and state.role == 'Seeker' and state.search_turns_left > 0:
                state.search_turns_left -= 1

                state.last_action_time = datetime.now(timezone.utc)
                state.last_active_post_time = datetime.now(timezone.utc)
                current_loc = state.current_location
                current_team = state.team


                found_hider = PlayerState.query.filter(
                    PlayerState.room_id == current_room_id,
                    PlayerState.role == 'Hider',
                    PlayerState.team != current_team,
                    PlayerState.current_location == current_loc
                ).first()

                if found_hider:
                    log_msg = f"Seeker named '{current_user.first_name}' ({state.team}) found a hider named '{found_hider.user.first_name}' ({found_hider.team}) in '{state.current_location}'. {state.team} WON!"
                    flash_msg = f"You found the Hider! {state.team} wins! Exam over."


                    winning_team_players = PlayerState.query.filter(
                        PlayerState.room_id == current_room_id,
                        PlayerState.team == state.team,
                        PlayerState.game_status == "Active"
                    ).all()

                    if winning_team_players:
                        points_per_player = 60 / len(winning_team_players)
                        points_per_player = round(points_per_player, 2)

                        for player_state in winning_team_players:
                            player_state.user.score += points_per_player
                            db.session.add(player_state.user)

                    end_game_and_cleanup_room(current_room_id, log_msg, flash_msg)
                    return redirect(url_for('views.game_rooms'))

                else:
                    flash(f"You sought the partial square '{state.current_location}'. No one is here.", "info")
                    create_game_log(state, f"Seeker {current_user.first_name} searched {current_loc} but found nothing.", privacy='private')

            elif action == 'detect' and state.role == 'Seeker' and state.detect_turns_left > 0:
                now_utc = datetime.now(timezone.utc)
                vietnam_tz_offset = timedelta(hours=7)
                now_vietnam = now_utc + vietnam_tz_offset
                current_hour_vietnam = now_vietnam.hour

                if not True: #(7 <= current_hour_vietnam < 22):
                    flash("You are just able to Detect in 7:00AM to 10:00PM in real time.", "error")
                    pass
                elif state.detect_turns_left <= 0:
                    flash("You ran out of turn to Detect.", "error")
                    pass
                else:
                    state.detect_turns_left -= 1
                    state.is_detecting = True
                    state.last_action_time = datetime.now(timezone.utc)
                    state.last_active_post_time = datetime.now(timezone.utc)
                    flash(f"Detect activated! You can now see opposing Seekers. (Turns left: {state.detect_turns_left})", "success")
                    create_game_log(state, f"Seeker named {current_user.first_name} used 'Detect' to reveal enemies.", privacy='team')


            elif action == 'gather' and state.role == 'Seeker' and state.gather_turns_left > 0:
                state.gather_turns_left -= 1
                state.last_action_time = datetime.now(timezone.utc)
                state.last_active_post_time = datetime.now(timezone.utc)

                current_herb_map = {}
                if state.room.daily_herb_mapping:
                    try:
                        current_herb_map = json.loads(state.room.daily_herb_mapping)
                    except:
                        current_herb_map = {}
                herb_found = current_herb_map.get(state.current_location)
                if herb_found == 'tuong_tu':
                    state.has_remote_water = True
                    flash("Congratulation! You gathered 'Tương tư đoạn trường thảo' successfully", "success")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'Tương tư đoạn trường thảo' successfully.", privacy='team')
                elif herb_found == 'thuong_quan':
                    state.has_teleport = True
                    flash("Congratulation! You gathered 'Thượng quan tử uyển thảo' successfully", "success")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'Thượng quan tử uyển thảo' successfully.", privacy='team')
                elif herb_found == 'quynh_tam':
                    state.has_quynh_tam_thao = True
                    flash("Congratulation! You gathered 'Quỳnh tâm hoán mệnh thảo' successfully.", "success")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'Quỳnh tâm hoán mệnh thảo' successfully.", privacy='team')
                elif herb_found == 'ly_sau':
                    state.has_ly_sau_thao = True
                    flash("Congratulation! You gathered 'Ly sầu tán phách thảo' successfully.", "success")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'Ly sầu tán phách thảo' successfully.", privacy='team')
                elif herb_found == 'nhat_nguyet':

                    enemy_seekers = PlayerState.query.filter(
                        PlayerState.room_id == current_room_id,
                        PlayerState.team != state.team,
                        PlayerState.role == 'Seeker'
                    ).all()

                    all_enemies_have_tracked = False
                    if enemy_seekers:

                        all_enemies_have_tracked = all(seeker.has_tracked for seeker in enemy_seekers)


                    if all_enemies_have_tracked:

                        flash("You gathered 'Nhật nguyệt tinh luân thảo', but all enemy Seekers have already tracked your Hider. The herb provides no effect.", "error")
                        create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'Nhật nguyệt tinh luân thảo', but it had no effect as the Hider was already tracked by all enemies.", privacy='public')
                    else:

                        state.has_nhat_nguyet_thao = True
                        flash("Congratulation! You gathered 'Nhật nguyệt tinh luân thảo' successfully. You are now immune with Detect action of all enemies.", "success")
                        create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'Nhật nguyệt tinh luân thảo' and is now IMMUNE to Detect.", privacy='public')

                elif state.current_location == '3g7':
                    if is_tram_tuong_spawned:
                        state.detect_turns_left += 1

                        flash(f"Congratulation! You gathered 'Trầm tương vọng nguyệt thảo'!", "success")
                        create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'Trầm tương vọng nguyệt thảo' in '3g7'.", privacy='team')
                    else:
                        flash(f"You gathered {state.current_location} but found nothing.", "info")
                        create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered in '3g7' but found nothing.", privacy='private')

                elif state.current_location == '2a2':
                    if state.gathered_seawater_today:
                        flash("The herbs here have all been picked. Please come back tomorrow.!", "info")
                        create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered at '{state.current_location}' but found nothing.", privacy='private')

                    else:
                        state.has_seawater_purifier = True
                        state.gathered_seawater_today = True

                        flash("Congratulation! You gathered 'Hải tâm thanh tịnh thảo' successfully.", "success")
                        create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'Hải tâm thanh tịnh thảo'.", privacy='team')

                elif herb_found == 'u_tam':
                    state.has_u_tam_thao = True
                    flash("Congratulation! You gathered 'U tâm tịch diệt thảo'. You now have ability to Set Trap.", "success")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'U tâm tịch diệt thảo'.", privacy='public')

                elif herb_found == 'phan_thien':
                    state.has_phan_thien_thao = True
                    flash("Congratulation! You gathered 'Phần Thiên Truy Long Thảo'.", "success")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered 'Phần Thiên Truy Long Thảo'.", privacy='public')

                else:
                    flash(f"You gathered the partial square '{state.current_location}'. Nothing is here.", "info")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) gathered at '{state.current_location}' but found nothing.", privacy='team')

            elif action == 'take_water' and state.role == 'Seeker':
                current_loc = state.current_location
                if state.take_water_turns_left <= 0:
                    flash("You ran out of turn to take water.", "error")

                elif current_loc not in FRESH_WATER_LOCATIONS:
                    flash("You are not in a partial square with water.", "error")

                elif state.current_water == 10.0:
                    flash("Your water bars is full already.", "info")

                else:
                    state.current_water = 10.0
                    state.take_water_turns_left -= 1
                    state.last_action_time = datetime.now(timezone.utc)
                    state.last_active_post_time = datetime.now(timezone.utc)

                    flash(f"Water bars refilled successfully!.", "success")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) took water in '{current_loc}'.", privacy='team')
            elif action == 'purify_water':

                current_main_square = state.current_location[1:]

                if not state.has_seawater_purifier:
                    flash("You do not have a 'Hải tâm thanh tịnh thảo'.", "error")

                elif not check_if_main_square_is_coastal(current_main_square):
                    flash("You need to stand on a main square having at least one seawater partial square to filtrate.", "error")

                else:
                    state.has_seawater_purifier = False
                    state.current_water = 10.0
                    state.last_action_time = datetime.now(timezone.utc)
                    state.last_active_post_time = datetime.now(timezone.utc)

                    flash(f"You used 'Hải tâm thanh tịnh thảo' to filtrate seawater in '{current_main_square}'. Water bars filled fully!", "success")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) used 'Hải tâm thanh tịnh thảo' in '{state.current_location}'.", privacy='team')

            elif action == 'set_trap' and state.role == 'Seeker':
                trap_coord = request.form.get('trap_coordinate')

                if not state.has_u_tam_thao:
                    flash("You do not have a 'U tâm tịch diệt thảo'.", "error")

                elif not trap_coord or parse_coordinate_safe(trap_coord) is None or trap_coord in SEAWATER_LOCATIONS:
                    flash("Invalid coordinate or Seawater coordinate.", "error")


                elif trap_coord in FRESH_WATER_LOCATIONS:
                    flash("You cannot set trap in a partial square with water.", "error")

                else:
                    state.has_u_tam_thao = False
                    state.active_trap_location = trap_coord
                    state.active_trap_time = datetime.now(timezone.utc)

                    state.last_action_time = datetime.now(timezone.utc)
                    state.last_active_post_time = datetime.now(timezone.utc)

                    flash(f"Set a trap successfully in '{trap_coord}'. It will exist for 48h.", "success")
                    create_game_log(state, f"Seeker '{current_user.first_name}' ({state.team}) set a deadly trap ('U tâm tịch diệt thảo') in somewhere on island. Be careful!", privacy='public')

            elif action == 'disclose_trace' and state.role == 'Seeker':
                target_id = request.form.get('target_id')

                if not state.has_phan_thien_thao:
                    flash("You do not have a 'Phần Thiên Truy Long Thảo'.", "error")

                elif not target_id:
                    flash("You have not selected a target to disclose yet.", "error")

                else:
                    target_state = PlayerState.query.filter_by(user_id=int(target_id)).first()

                    if not target_state or target_state.room_id != current_room_id or target_state.team == state.team:
                        flash("Targer is invalid!", "error")
                    else:
                        now_utc = datetime.now(timezone.utc)
                        vietnam_tz_offset = timedelta(hours=7)
                        now_vietnam = now_utc + vietnam_tz_offset

                        start_of_day_vn = now_vietnam.replace(hour=0, minute=0, second=0, microsecond=0)

                        start_of_day_utc = start_of_day_vn - vietnam_tz_offset

                        movement_logs = GameLog.query.filter(
                            GameLog.user_id == target_state.user_id,
                            GameLog.room_id == current_room_id,
                            GameLog.timestamp >= start_of_day_utc,
                            GameLog.log_message.contains("moved from")
                        ).order_by(GameLog.timestamp.asc()).all()

                        if not movement_logs:
                            history_str = "There is not any move today."
                        else:
                            history_steps = []
                            for log in movement_logs:
                                log_time_vn = log.timestamp + vietnam_tz_offset
                                time_str = log_time_vn.strftime('%H:%M')

                                raw_msg = log.log_message

                                if "moved from" in raw_msg:
                                    clean_msg = raw_msg.split(" moved ")[-1]
                                    if clean_msg.endswith('.'):
                                        clean_msg = clean_msg[:-1]
                                else:
                                    clean_msg = raw_msg

                                history_steps.append(f"[{time_str}] {clean_msg}")

                            history_str = " | ".join(history_steps)


                        state.has_phan_thien_thao = False

                        public_msg = f"Seeker '{current_user.first_name}' ({state.team}) disclosed all traces of '{target_state.user.first_name}' ({target_state.team}) today. TARGET MOVING'S HISTORY: {history_str}"

                        create_game_log(state, public_msg, privacy='public')

                        flash(f"Disclosed '{target_state.user.first_name}' successfully!", "success")

            elif action == 'transfer_water':
                try:
                    receiver_id = request.form.get('receiver_id')
                    amount_str = request.form.get('amount')

                    if not receiver_id or not amount_str:
                        flash("Receiver and amount are required.", "error")
                        return redirect(url_for('views.game_dashboard'))

                    amount = round(float(amount_str), 2)
                    receiver_state = PlayerState.query.with_for_update().filter_by(user_id=int(receiver_id),room_id=current_room_id).first()


                    max_transfer = round(state.current_water - 0.5, 2)
                    if amount <= 0:
                        flash("Transfer amount must be greater than 0.", "error")
                    elif amount > max_transfer:
                        flash(f"You can only transfer a maximum of {max_transfer} water bars.", "error")


                    elif not receiver_state:
                        flash("Receiver not found.", "error")
                    elif receiver_state.room_id != current_room_id:
                        flash("Receiver is not in your room.", "error")
                    elif receiver_state.team != state.team:
                        flash("You can only transfer water to your teammates.", "error")
                    else:
                        is_local = (receiver_state.current_location == state.current_location)
                        is_remote = not is_local

                        if is_remote and not state.has_remote_water:

                            flash(f"You must be at the same location '({receiver_state.current_location})' as '{receiver_state.user.first_name}' to transfer water.", "error")
                        else:

                            state.current_water -= amount
                            receiver_state.current_water += amount
                            if receiver_state.current_water > 10.0:
                                receiver_state.current_water = 10.0
                            state.last_action_time = datetime.now(timezone.utc)
                            state.last_active_post_time = datetime.now(timezone.utc)
                            log_prefix = "LOCAL"


                            if is_remote and state.has_remote_water:
                                state.has_remote_water = False
                                log_prefix = "REMOTE (Item consumed)"

                            create_game_log(state, f"Seeker '{current_user.first_name}' ({log_prefix}) transfered {amount} water to '{receiver_state.user.first_name}'.", privacy='team')
                            flash(f"Successfully transferred {amount} water to {receiver_state.user.first_name}!", "success")

                except ValueError:
                    flash("Invalid amount. Please enter a valid number.", "error")
                except Exception as e:
                    db.session.rollback()
                    flash(f"An error occurred during transfer: {e}", "error")

            elif action == 'teleport':

                if not state.has_teleport or state.role != 'Seeker':
                    flash("You do not have the 'Thượng quan tử uyển thảo' item.", "error")
                    return redirect(url_for('views.game_dashboard'))


                new_loc = request.form.get('teleport_location')

                if not new_loc or parse_coordinate_safe(new_loc) is None:
                    flash("Invalid coordinate format.", "error")
                elif new_loc in SEAWATER_LOCATIONS:
                    flash(f"Can't teleport to '{new_loc}' because it is seawater!", "error")
                else:

                    current_loc = state.current_location
                    state.current_location = new_loc
                    state.has_teleport = False
                    state.last_action_time = datetime.now(timezone.utc)
                    state.last_active_post_time = datetime.now(timezone.utc)
                    state.is_detecting = False

                    if check_and_trigger_traps(state, current_room_id):
                            flash("BOOM! You have stepped into the enemy's trap.! Lose 3.0 water bars.", "error")

                            if state.current_water <= 0:
                                if state.has_quynh_tam_thao:
                                    state.has_quynh_tam_thao = False
                                    state.current_water = 5.0
                                    flash("Trap make you run out of water, but 'Quỳnh tâm hoán mệnh thảo' saved you!", "success")
                                    create_game_log(state, f"Player '{current_user.first_name}' used Quỳnh tâm hoán mệnh thảo to survive after stepping on a trap.", privacy='public')
                                else:
                                    state.current_water = 0
                                    state.game_status = "Eliminated (Trap)"
                                    log_msg = f"Seeker '{current_user.first_name}' ({state.team}) is terminated due to step into a trap."
                                    create_game_log(state, log_msg, privacy='public')
                                    db.session.delete(state)
                                    db.session.commit()
                                    return redirect(url_for('views.game_rooms'))

                    flash(f"Successfully teleported from {current_loc} to {new_loc}! Item consumed.", "success")
                    create_game_log(state, f"Seeker named '{current_user.first_name}' ({state.team}) did teleport from '{current_loc}' to '{new_loc}' (Item consumed).", privacy='team')

            elif action == 'track':
                active_afk_hours = (datetime.now(timezone.utc) - last_active_time).total_seconds() / 3600

                if active_afk_hours <= 12 or state.role != 'Seeker':
                    flash("You are not eligible to use this action yet.", "error")
                else:

                    enemy_hider = PlayerState.query.filter(
                        PlayerState.room_id == current_room_id,
                        PlayerState.team != state.team,
                        PlayerState.role == 'Hider'
                    ).first()

                    if enemy_hider:
                        seeker_main_square = state.current_location[1:]
                        hider_main_square = enemy_hider.current_location[1:]
                        hider_super_square_zone = get_super_square(hider_main_square)

                        if seeker_main_square in hider_super_square_zone:
                            state.has_tracked = True
                            flash("Your senses are sharp! You feel the Hider is nearby.", "success")
                            create_game_log(state, f"Seeker {current_user.first_name} ({state.team}) used Tracker and sensed the Hider is nearby!", privacy='public')
                            hiders_team_id = enemy_hider.team


                            hiders_team_players = PlayerState.query.filter_by(
                                room_id=current_room_id,
                                team=hiders_team_id
                            ).all()

                            item_was_dispelled = False
                            for player in hiders_team_players:
                                if player.has_nhat_nguyet_thao:
                                    player.has_nhat_nguyet_thao = False
                                    item_was_dispelled = True
                            if item_was_dispelled:
                                log_msg_dispel = f"The Hider's team was successfully tracked! All 'Nhật nguyệt tinh luân thảo' immunity effects on that team have been dispelled."
                                create_game_log(state, log_msg_dispel, privacy='public')
                        else:
                            flash("You sense nothing. The Hider is not in this super square.", "info")
                            create_game_log(state, f"Seeker {current_user.first_name} used Tracker but sensed nothing.", privacy='private')
                    else:
                        flash("There is no Hider to track.", "error")


                    state.last_active_post_time = datetime.now(timezone.utc)

            elif action == 'emit_signal' and state.role == 'Hider':

                if state.has_used_gambit:
                    flash("You do not have any turn to GAMBIT left.", "error")
                else:

                    state.has_used_gambit = True
                    state.last_action_time = datetime.now(timezone.utc)
                    state.last_active_post_time = datetime.now(timezone.utc)

                    hider_main_square = state.current_location[1:]
                    log_msg = f"HIDER'S GAMBIT! Hider '{current_user.first_name}' ({state.team}) activated the Hider's GAMBIT. This hider is in '{hider_main_square}'."
                    create_game_log(state, log_msg, privacy='public')


                    teammate_seekers = PlayerState.query.filter(
                        PlayerState.room_id == current_room_id,
                        PlayerState.team == state.team,
                        PlayerState.role == 'Seeker',
                        PlayerState.game_status == 'Active'
                    ).all()

                    buffed_seekers = []
                    for seeker in teammate_seekers:
                        seeker.search_turns_left += 1
                        seeker.gather_turns_left += 1
                        buffed_seekers.append(seeker.user.first_name)

                    if buffed_seekers:
                        flash_msg = f"GAMBIT successfully! Your main square is revealed. Teammates: {', '.join(buffed_seekers)} got 1 search turn and 1 gather turn for each."
                        create_game_log(state, f"All seekers ({state.team}) got +1 Search/+1 Gather from Hider's Gambit.", privacy='team')
                    else:
                        flash_msg = "GAMBIT successfully! Your main square is revealed. (There is no teammate left to get this buff)."

                    flash(flash_msg, "success")

            elif action == 'surrender' and state.role == 'Hider':

                log_msg = f"Hider named '{current_user.first_name}' ({state.team}) has resigned. {state.team} LOST!. Room '{state.room.room_name}' is terminated."
                flash_msg = f"You surrendered. {state.team} loses. Exam over."

                losing_seekers = PlayerState.query.filter(
                    PlayerState.room_id == current_room_id,
                    PlayerState.team == state.team,
                    PlayerState.role == 'Seeker'
                ).all()

                for seeker_state in losing_seekers:
                    seeker_state.user.score -= 10
                    db.session.add(seeker_state.user)

                end_game_and_cleanup_room(current_room_id, log_msg, flash_msg)

                return redirect(url_for('views.game_rooms'))

            elif action == 'restore':
                if current_user.id == state.room.host_id:
                    log_msg = f"HOST '{current_user.first_name}' has reset the game room."
                    flash_msg = f"Room has been reset by Host."
                    end_game_and_cleanup_room(current_room_id, log_msg, flash_msg)
                    return redirect(url_for('views.game_rooms'))
                else:
                    flash("Only the Room Host can restore the game!", "error")
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(f"An error occurred on commit: {e}", "error")
                return redirect(url_for('views.game_dashboard'))
            return redirect(url_for('views.game_dashboard'))

    # GET request
    teammates = PlayerState.query.options(joinedload(PlayerState.user)).filter_by(
        room_id=current_room_id,
        team=state.team
    ).all()

    enemies = PlayerState.query.options(joinedload(PlayerState.user)).filter(
        PlayerState.room_id == current_room_id,
        PlayerState.team != state.team,
        PlayerState.role == 'Seeker'
    ).all()

    can_take_water = False
    current_loc = state.current_location



    if state.role == 'Seeker' and current_loc in FRESH_WATER_LOCATIONS:

        can_take_water = True

    teammates_at_location = PlayerState.query.options(joinedload(PlayerState.user)).filter(
        PlayerState.room_id == current_room_id,
        PlayerState.team == state.team,
        PlayerState.current_location == state.current_location,
        PlayerState.user_id != current_user.id
    ).all()

    all_teammates = PlayerState.query.options(joinedload(PlayerState.user)).filter(
        PlayerState.room_id == current_room_id,
        PlayerState.team == state.team,
        PlayerState.user_id != current_user.id
    ).all()

    max_transfer = round(state.current_water - 0.5, 2)
    can_transfer_local = len(teammates_at_location) > 0
    can_transfer_remote = state.has_remote_water


    show_transfer_button = (can_transfer_local or can_transfer_remote) and max_transfer > 0
    show_teleport_button = state.has_teleport and state.role == 'Seeker'
    active_afk_hours = (datetime.now(timezone.utc) - last_active_time).total_seconds() / 3600
    show_track_button = (active_afk_hours > 12 and state.role == 'Seeker' and state.has_tracked == False)
    show_gambit_button = (state.role == 'Hider' and not state.has_used_gambit)
    current_main_square = state.current_location[1:]
    can_purify_here = check_if_main_square_is_coastal(current_main_square)
    show_purify_button = (state.has_seawater_purifier and can_purify_here)

    beast_locations = [state.room.beast_square_1, state.room.beast_square_2]
    plot_image = generate_game_map_plot(state, teammates, enemies, state.is_detecting, beast_locations)



    return render_template('simulate_se_3.html',
                            user=current_user,
                            state=state,
                            plot_image=plot_image,
                            players_in_room=teammates + enemies,
                            is_gamemaster=False,
                            can_take_water=can_take_water,
                            show_transfer_button=show_transfer_button,
                            all_teammates=all_teammates,
                            max_transferable_water=max_transfer,
                            teammates_at_loc=teammates_at_location,
                            show_teleport_button=show_teleport_button,
                            violence_enabled=state.room.violence_enabled,
                            show_track_button=show_track_button,
                            show_gambit_button=show_gambit_button,
                            show_purify_button=show_purify_button
                            )


@views.route('/leaderboard')
@login_required
def leaderboard():
    top_users = User.query.order_by(User.score.desc()).limit(10).all()

    return render_template('simulate_se_3_leaderboard.html', user=current_user, users=top_users)



@views.route('/api/send_chat_message', methods=['POST'])
@login_required
def send_chat_message():
    state = current_user.player_state
    if not state:
        return jsonify({'success': False, 'message': 'You are not in a game.'}), 403

    data = request.get_json()
    message_body = data.get('message_body')
    scope = data.get('scope')

    target_team = data.get('target_team')
    if not message_body or len(message_body.strip()) == 0:
        return jsonify({'success': False, 'message': 'Empty message.'}), 400

    if scope not in ['team', 'global']:
        return jsonify({'success': False, 'message': 'Invalid scope.'}), 400


    team_id_to_store = None
    if scope == 'team':
        if state.role == 'Gamemaster':
            if target_team in ['TeamA', 'TeamB']:
                team_id_to_store = target_team
            else:
                return jsonify({'success': False, 'message': 'Host must specify TeamA or TeamB.'}), 400
        else:
            team_id_to_store = state.team

    try:
        new_message = GameChat(
            message_body=message_body.strip(),
            user_id=current_user.id,
            room_id=state.room_id,
            scope=scope,
            team_id=team_id_to_store
        )
        db.session.add(new_message)
        db.session.commit()

        return jsonify({'success': True, 'message': 'Sent!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500




@views.route('/api/toggle_violence', methods=['POST'])
@login_required
def toggle_violence():
    state = current_user.player_state
    if not state:
        return jsonify({'success': False, 'message': 'Player not in a game.'}), 404

    room = state.room


    if room.host_id != current_user.id:
        return jsonify({'success': False, 'message': 'Only the room host can change this setting.'}), 403

    try:
        data = request.get_json()
        new_status = bool(data.get('enabled'))

        room.violence_enabled = new_status
        db.session.commit()


        log_status = "ON" if new_status else "OFF"
        log_msg = f"The host named '{current_user.first_name}' turned {log_status} violence feature for '{room.room_name}'."
        new_log = GameLog(log_message=log_msg, user_id=current_user.id)
        db.session.add(new_log)
        db.session.commit()

        return jsonify({'success': True, 'new_status': new_status})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500




@views.route('/api/get_notifications')
@login_required
def get_notifications():



    unread_notifications = Notification.query.filter_by(
        user_id=current_user.id,
        is_read=False
    ).order_by(Notification.timestamp.asc()).all()

    if not unread_notifications:
        return jsonify([])


    notification_list = []
    for notif in unread_notifications:
        notification_list.append({
            'id': notif.id,
            'message': notif.message,
            'timestamp': notif.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        })


        notif.is_read = True


    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error while marking checked notification: {e}")
        return jsonify({'error': str(e)}), 500


    return jsonify(notification_list)



@views.route('/api/get_activity_feed')
@login_required
def get_activity_feed():
    state = current_user.player_state
    if not state:
        return jsonify({'team_logs': [], 'global_logs': [], 'team_chat': [], 'global_chat': []})

    room_id = state.room_id
    vietnam_tz_offset = timedelta(hours=7)

    def log_to_dict(log):
        local_time = log.timestamp + vietnam_tz_offset
        return {'message': log.log_message, 'timestamp': local_time.strftime('%d/%m %H:%M:%S')}

    def chat_to_dict(msg):
        local_time = msg.timestamp + vietnam_tz_offset
        return {
            'user_name': msg.user.first_name,
            'message': msg.message_body,
            'timestamp': local_time.strftime('%d/%m %H:%M:%S'),
            'is_self': msg.user_id == current_user.id
        }


    global_logs = [log_to_dict(l) for l in GameLog.query.filter_by(room_id=room_id, privacy='public').order_by(GameLog.timestamp.desc()).limit(20).all()]
    global_chat = [chat_to_dict(m) for m in reversed(GameChat.query.options(joinedload(GameChat.user)).filter_by(room_id=room_id, scope='global').order_by(GameChat.timestamp.desc()).limit(20).all())]

    response_data = {
        'global_logs': global_logs,
        'global_chat': global_chat,
        'is_gamemaster': (state.role == 'Gamemaster')
    }

    if state.role == 'Gamemaster':



        logs_a = GameLog.query.filter_by(room_id=room_id, team_id='TeamA', privacy='team').order_by(GameLog.timestamp.desc()).limit(20).all()
        chat_a = GameChat.query.options(joinedload(GameChat.user)).filter_by(room_id=room_id, scope='team', team_id='TeamA').order_by(GameChat.timestamp.desc()).limit(20).all()


        logs_b = GameLog.query.filter_by(room_id=room_id, team_id='TeamB', privacy='team').order_by(GameLog.timestamp.desc()).limit(20).all()
        chat_b = GameChat.query.options(joinedload(GameChat.user)).filter_by(room_id=room_id, scope='team', team_id='TeamB').order_by(GameChat.timestamp.desc()).limit(20).all()

        response_data['team_a_logs'] = [log_to_dict(l) for l in logs_a]
        response_data['team_a_chat'] = [chat_to_dict(m) for m in reversed(chat_a)]
        response_data['team_b_logs'] = [log_to_dict(l) for l in logs_b]
        response_data['team_b_chat'] = [chat_to_dict(m) for m in reversed(chat_b)]

    else:
        team_id = state.team
        team_logs = GameLog.query.filter(
            GameLog.room_id == room_id,
            GameLog.team_id == team_id,
            (GameLog.privacy == 'team') | (GameLog.user_id == current_user.id)
        ).order_by(GameLog.timestamp.desc()).limit(20).all()

        team_chat = GameChat.query.options(joinedload(GameChat.user)).filter(
            GameChat.room_id == room_id, GameChat.scope == 'team', GameChat.team_id == team_id
        ).order_by(GameChat.timestamp.desc()).limit(20).all()

        response_data['team_logs'] = [log_to_dict(l) for l in team_logs]
        response_data['team_chat'] = [chat_to_dict(m) for m in reversed(team_chat)]

    return jsonify(response_data)




    team_chat_query = GameChat.query.options(joinedload(GameChat.user)).filter(
        GameChat.room_id == room_id,
        GameChat.scope == 'team',
        GameChat.team_id == team_id
    ).order_by(GameChat.timestamp.desc()).limit(20).all()


    global_chat_query = GameChat.query.options(joinedload(GameChat.user)).filter(
        GameChat.room_id == room_id,
        GameChat.scope == 'global'
    ).order_by(GameChat.timestamp.desc()).limit(20).all()


    def chat_to_dict(msg):
        local_time = msg.timestamp + vietnam_tz_offset
        return {
            'user_name': msg.user.first_name,
            'message': msg.message_body,
            'timestamp': local_time.strftime('%d/%m %H:%M:%S'),
            'is_self': msg.user_id == current_user.id
        }


    team_chat = [chat_to_dict(msg) for msg in reversed(team_chat_query)]
    global_chat = [chat_to_dict(msg) for msg in reversed(global_chat_query)]


    return jsonify({
        'team_logs': team_logs,
        'global_logs': global_logs,
        'team_chat': team_chat,
        'global_chat': global_chat
    })