from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, jsonify
from .models import User
from werkzeug.security import generate_password_hash, check_password_hash
from . import db
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
import os
from datetime import datetime

auth = Blueprint('auth', __name__)


@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':                   #Kiểm tra xem có tài khoản đã được log in rồi thì thôi không cần sign in nữa
        email = request.form.get('email')
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()    #Tìm xem trong db có email này không?
        if user:                                             #Nếu không tức là invalid vì tk chưa tồn tại
            if check_password_hash(user.password, password):
                flash("Logged in successfully!", category='success_center')
                login_user(user, remember=True)            #Giữ user login (đến khi restart webserver?)
                return redirect(url_for('views.home'))
            else:
                flash("Incorrect password, try again!", category='error')
        else:
            flash("Email does not exist!", category='error')


    return render_template("login.html", user=current_user)

@auth.route('/logout')
@login_required                               #Cần login mới thấy được Logout
def logout():
    logout_user()
    flash("Logged out!", category='success_center')
    return redirect(url_for('auth.login'))

@auth.route('/sign-up', methods=['GET', 'POST'])
def sign_up():
    if request.method == 'POST':
        email = request.form.get('email')
        first_name = request.form.get('firstName')
        password1 = request.form.get('password1')
        password2 = request.form.get('password2')
        dob_string = request.form.get('dob')

        user = User.query.filter_by(email=email).first()


        dob_object = None
        if not dob_string:
            flash("Date of birth is required.", category='error')
        else:
            try:

                dob_object = datetime.strptime(dob_string, '%Y-%m-%d').date()
            except ValueError:
                flash("Invalid date format.", category='error')

        if user:
            flash("Email already exists", category='error')
        elif len(email) < 4:
            flash("Email must be greater than 3 characters.", category='error')
        elif len(first_name) < 2:
            flash("First name must be greater than 1 character.", category='error')
        elif len(password1) < 5:
            flash("Password must be greater than 4 characters.", category='error')
        elif password1 != password2:
            flash("Password don't match.", category='error')
        elif not dob_object:

            pass
        else:

            new_user = User(
                email=email,
                first_name=first_name,
                password=generate_password_hash(password1, method='pbkdf2:sha256'),
                date_of_birth=dob_object
            )

            db.session.add(new_user)
            db.session.commit()

            login_user(new_user, remember=True)
            flash("Account created!", category='success_center')
            return redirect(url_for('views.home'))

    return render_template("sign_up.html", user=current_user)



@auth.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':

        new_first_name = request.form.get('first_name')
        if new_first_name and len(new_first_name) >= 2:
            current_user.first_name = new_first_name
        else:
            flash('First name must be greater than 1 character.', category='error')


        if 'avatar' in request.files:
            file = request.files['avatar']


            if file.filename != '':

                filename, file_extension = os.path.splitext(file.filename)

                new_filename = f"user_{current_user.id}{file_extension}"


                upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], new_filename)

                try:
                    file.save(upload_path)

                    current_user.avatar_image = new_filename
                    flash('Avatar updated successfully!', category='success_center')
                except Exception as e:
                    flash(f'Error saving avatar: {e}', category='error')

        dob_string = request.form.get('dob')
        if dob_string:
            try:
                dob_object = datetime.strptime(dob_string, '%Y-%m-%d').date()
                current_user.date_of_birth = dob_object
            except ValueError:
                flash('Invalid date format provided.', category='error')
        else:
            current_user.date_of_birth = None

        try:
            db.session.commit()
            flash('Profile updated!', category='success_center')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating profile: {e}', category='error')

        return redirect(url_for('auth.profile'))


    return render_template('profile.html', user=current_user)




@auth.route('/api/check-password', methods=['POST'])
@login_required
def check_password():

    data = request.get_json()
    password_to_check = data.get('password')

    if not password_to_check:
        return jsonify({'success': False, 'message': 'Password is required.'}), 400


    secret_password = current_app.config['PAGE_PROTECT_PASSWORD']


    if password_to_check == secret_password:

        return jsonify({'success': True})
    else:

        return jsonify({'success': False, 'message': 'Incorrect password. Please try again.'}), 401
