from . import db
from flask_login import UserMixin
from sqlalchemy.sql import func
from sqlalchemy.ext.hybrid import hybrid_property
import math
from datetime import datetime, timezone, timedelta


class GameRoom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_name = db.Column(db.String(100), nullable=False)
    host_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    host = db.relationship('User')
    status = db.Column(db.String(20), default='waiting')
    date_created = db.Column(db.DateTime(timezone=True), default=func.now())
    violence_enabled = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    beast_square_1 = db.Column(db.String(5), nullable=True)
    beast_square_2 = db.Column(db.String(5), nullable=True)

    tram_tuong_herb_day = db.Column(db.Date, nullable=True)
    tram_tuong_herb_minute = db.Column(db.Integer, nullable=True)

    daily_herb_spawn_date = db.Column(db.Date, nullable=True)
    daily_herb_mapping = db.Column(db.Text, nullable=True)

    mode = db.Column(db.String(20), default='simulation')
    players = db.relationship('PlayerState', back_populates='room', lazy='dynamic')




class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True)
    password = db.Column(db.String(150))
    first_name = db.Column(db.String(150))
    notes = db.relationship('Note')
    date_created = db.Column(db.DateTime(timezone=True), default=func.now())
    role = db.Column(db.String(50), default="User")
    score = db.Column(db.Integer, default=0)
    avatar_image = db.Column(db.String(150), nullable=False, server_default='default.jpg')
    hosted_rooms = db.relationship('GameRoom', back_populates='host')
    date_of_birth = db.Column(db.Date, nullable=True)


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.String(10000))
    date = db.Column(db.DateTime(timezone=True), default=func.now())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

class PlayerState(db.Model):
    id = db.Column(db.Integer, primary_key=True)


    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True)
    user = db.relationship('User', backref=db.backref('player_state', uselist=False))

    room_id = db.Column(db.Integer, db.ForeignKey('game_room.id'), nullable=False)
    room = db.relationship('GameRoom', back_populates='players')


    game_status = db.Column(db.String(50), default="Active")
    team = db.Column(db.String(10))
    role = db.Column(db.String(50))


    current_location = db.Column(db.String(10), default="1e6")
    current_water = db.Column(db.Float, default=10.0)

    last_action_time = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active_post_time = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    search_turns_left = db.Column(db.Integer, default=1)
    _gather_turns_left = db.Column("gather_turns_left", db.Integer, default=2)

    has_remote_water = db.Column(db.Boolean, default=False)
    has_teleport = db.Column(db.Boolean, default=False)
    has_tracked = db.Column(db.Boolean, default=False)
    has_quynh_tam_thao = db.Column(db.Boolean, default=False)
    has_ly_sau_thao = db.Column(db.Boolean, default=False)
    has_nhat_nguyet_thao = db.Column(db.Boolean, default=False)
    has_used_gambit = db.Column(db.Boolean, default=False)
    has_seawater_purifier = db.Column(db.Boolean, default=False)
    has_u_tam_thao = db.Column(db.Boolean, default=False)
    has_phan_thien_thao = db.Column(db.Boolean, default=False)

    active_trap_location = db.Column(db.String(10), nullable=True)
    active_trap_time = db.Column(db.DateTime(timezone=True), nullable=True)

    spirit_class = db.Column(db.String(20), nullable=True)
    stun_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)

    is_detecting = db.Column(db.Boolean, default=False)

    _take_water_turns_left = db.Column("take_water_turns_left", db.Integer, default=1)
    _detect_turns_left = db.Column("detect_turns_left", db.Integer, default=2)
    _last_detect_reset = db.Column("last_detect_reset", db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    _gathered_seawater_today = db.Column("gathered_seawater_today", db.Boolean, default=False)

    def _check_daily_reset(self):
        now_utc = datetime.now(timezone.utc)
        vietnam_tz_offset = timedelta(hours=7)
        now_vietnam = now_utc + vietnam_tz_offset
        today_vietnam_date = now_vietnam.date()

        last_reset_utc = self._last_detect_reset

        if last_reset_utc is None:
            self._last_detect_reset = now_utc
            last_reset_utc = now_utc

        if last_reset_utc.tzinfo is None:
            last_reset_utc = last_reset_utc.replace(tzinfo=timezone.utc)

        last_reset_vietnam = last_reset_utc + vietnam_tz_offset
        last_reset_vietnam_date = last_reset_vietnam.date()

        if today_vietnam_date > last_reset_vietnam_date:
            self._detect_turns_left = 2
            self._take_water_turns_left = 1
            self._gather_turns_left = 2
            self._gathered_seawater_today = False


            self._last_detect_reset = now_utc
            return True
        return False

    @hybrid_property
    def detect_turns_left(self):
        self._check_daily_reset()
        return self._detect_turns_left

    @detect_turns_left.setter
    def detect_turns_left(self, value):
        self._detect_turns_left = value


    @hybrid_property
    def take_water_turns_left(self):
        self._check_daily_reset()
        return self._take_water_turns_left

    @take_water_turns_left.setter
    def take_water_turns_left(self, value):
        self._take_water_turns_left = value

    @hybrid_property
    def gather_turns_left(self):
        self._check_daily_reset()
        return self._gather_turns_left

    @gather_turns_left.setter
    def gather_turns_left(self, value):
        self._gather_turns_left = value

    @hybrid_property
    def gathered_seawater_today(self):
        self._check_daily_reset()
        return self._gathered_seawater_today

    @gathered_seawater_today.setter
    def gathered_seawater_today(self, value):
        self._gathered_seawater_today = value



class GameLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    log_message = db.Column(db.String(500))


    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User')


    room_id = db.Column(db.Integer, db.ForeignKey('game_room.id'), nullable=True)

    team_id = db.Column(db.String(10), nullable=True)


    privacy = db.Column(db.String(20), default='team')


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User')

    message = db.Column(db.String(500))


    is_read = db.Column(db.Boolean, default=False)

    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))



class GameChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    message_body = db.Column(db.String(500), nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User')

    room_id = db.Column(db.Integer, db.ForeignKey('game_room.id'), nullable=False)

    scope = db.Column(db.String(20), default='team')

    team_id = db.Column(db.String(10), nullable=True)


#QUAN TRỌNG: MỖI LẦN SỬA FILE NÀY HÃY CHẠY 4 LỆNH NÀY TRONG TERMINAL
#b1 cd App
#b2 $env:FLASK_APP = "website"
#b3 flask db migrate -m "Write down your purpose (Optional)"
#b4 flask db upgrade