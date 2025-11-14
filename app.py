from flask import Flask, render_template, url_for, redirect, request, flash, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import InputRequired, Length, ValidationError
from datetime import date, datetime
from sqlalchemy import func
import os
from dotenv import load_dotenv

# load .env file
load_dotenv()

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URI")
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY")

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

CATEGORIES = ['Food', 'Transport', 'Entertainment', 'Utilities', 'Health', 'Subscriptions', 'Other']

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    # relationship: one category, many expenses
    expenses = db.relationship('Expense', backref='category', lazy=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(20), nullable=True)

class RegistrationForm(FlaskForm):
    username = StringField(validators=[InputRequired(), Length(min=4, max=20)], render_kw={"placeholder": "Username"})
    password = PasswordField(validators=[InputRequired(), Length(min=4, max=20)], render_kw={"placeholder": "Password"})
    submit = SubmitField("Register")

    def validate_username(self, username):
        existing_user = User.query.filter_by(username=username.data).first()
        if existing_user:
            raise ValidationError("That username already exists. Please choose a different one.")

class LoginForm(FlaskForm):
    username = StringField(validators=[InputRequired(), Length(min=4, max=20)], render_kw={"placeholder": "Username"})
    password = PasswordField(validators=[InputRequired(), Length(min=4, max=20)], render_kw={"placeholder": "Password"})
    submit = SubmitField("Login")

class CustomizeForm(FlaskForm):
    name = StringField(validators=[InputRequired(), Length(min=1, max=20)], render_kw={"placeholder": "Name"})
    submit = SubmitField("Submit")
    
class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and bcrypt.check_password_hash(user.password, form.password.data):
            login_user(user)
            if not user.name:
                return redirect(url_for('customize'))
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid username or password", "danger")
    return render_template('login.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegistrationForm()
    existing_user = User.query.filter_by(username=form.username.data).first()
    if existing_user:
        flash("Username already taken. Please choose another.", "danger")
        return redirect(url_for('register'))
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        new_user = User(username=form.username.data, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        init_categories_for_user(new_user.id)
        return redirect(url_for('login'))
    return render_template('register.html', form=form)

@app.route('/customize', methods=['GET', 'POST'])
@login_required
def customize():
    form = CustomizeForm()
    if form.validate_on_submit():
        current_user.name = form.name.data
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('customize.html', form=form)

def parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return None

@app.route('/dashboard')
@login_required
def dashboard():
    # Check if user wants to clear filters
    clear_filters = request.args.get('clear_filters') == 'true'
    
	# Categories chart
    cat_rows = (
        db.session.query(Category.name, func.sum(Expense.amount))
        .join(Category, Expense.category_id == Category.id)
        .filter(Expense.user_id == current_user.id)
        .group_by(Category.name)
        .all()
	)
    cat_labels = [c for c, _ in cat_rows]
    cat_values = [round(float((s or 0)), 2) for _, s in cat_rows]
    
	# Day chart
    day_q = (
        db.session.query(Expense.date, func.sum(Expense.amount))
        .filter(Expense.user_id == current_user.id)
	)
    day_rows = day_q.group_by(Expense.date).all()
    day_labels = [d.isoformat() for d, _ in day_rows]
    day_values = [round(float((s or 0)), 2) for _, s in day_rows]

    if clear_filters:
        # Just return all expenses for current user
        expenses = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.date.desc()).all()
        categories = Category.query.filter_by(user_id=current_user.id).all()
        total = round(sum(e.amount for e in expenses), 2)
        return render_template(
            'dashboard.html',
            name=current_user.name,
            expenses=expenses,
            categories=categories,
            total=total,
            start='',
            end='',
            min='',
            max='',
            selected_category='',
            today=date.today().isoformat(),
        	cat_labels=cat_labels,
			cat_values=cat_values,
        	day_labels=day_labels,
			day_values=day_values
        )

    # otherwise, handle normal filters below
    start_str = (request.args.get('start') or '').strip()
    end_str = (request.args.get('end') or '').strip()
    start_date = parse_date(start_str)
    end_date = parse_date(end_str)
    selected_category = (request.args.get('filter_category') or '').strip()

    q = Expense.query.filter_by(user_id=current_user.id)

    if start_date:
        q = q.filter(Expense.date >= start_date)
    if end_date:
        q = q.filter(Expense.date <= end_date)

    min_amount = (request.args.get('min_amount') or '').strip()
    max_amount = (request.args.get('max_amount') or '').strip()

    if min_amount:
        q = q.filter(Expense.amount >= float(min_amount))
    if max_amount:
        q = q.filter(Expense.amount <= float(max_amount))

    selected_category = (request.args.get('filter_category') or '').strip()
 
    if selected_category:
        q = q.join(Category).filter(Category.name == selected_category)


    expenses = q.order_by(Expense.date.desc()).all()
    total = round(sum(e.amount for e in expenses), 2)

    categories = Category.query.filter_by(user_id=current_user.id).all()
    
    print(expenses)

    return render_template(
        'dashboard.html',
        name=current_user.name,
        expenses=expenses,
        categories=categories,
        total=total,
        start=start_str,
        end=end_str,
        min=min_amount,
        max=max_amount,
        selected_category=selected_category,
        today=date.today().isoformat(),
        cat_labels=cat_labels,
		cat_values=cat_values,
        day_labels=day_labels,
		day_values=day_values
    )

@app.route('/edit_categories')
@login_required
def manage_categories():
    categories = Category.query.filter_by(user_id=current_user.id).order_by(Category.name).all()
    return render_template('manage_cats.html', categories=categories)

@app.route('/edit_categories/add', methods=['POST'])
@login_required
def add_category():
    new_name = request.form.get("new_category", "").strip()

    if not new_name:
        flash("Name cannot be empty.", "error")
        return redirect(url_for('manage_categories'))
    
    existing = Category.query.filter_by(
        name=new_name,
        user_id=current_user.id
    ).first()

    # Check for duplicates

    if existing:
        flash("Category with this name already exists.", "error")
        return redirect(url_for('manage_categories'))

    cat = Category(name=new_name, user_id=current_user.id)
    db.session.add(cat)
    db.session.commit()
    flash("Category added!", "success")
    return redirect(url_for('manage_categories'))

@app.route('/edit_categories/<int:cat_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_category(cat_id):
    new_name = request.form.get("name", "").strip()
    cat = Category.query.get_or_404(cat_id)

    if not new_name:
        flash("Name cannot be empty.", "error")
        return redirect(url_for('manage_categories'))

    existing = Category.query.filter_by(
        name=new_name,
        user_id=current_user.id
	).first()

	# Check for duplicates

    if existing:
        flash("Category with this name already exists.", "error")

    cat.name = new_name
    db.session.commit()
    flash("Category renamed!", "success")
    return redirect(url_for('manage_categories'))


@app.route('/edit_categories/<int:cat_id>/delete', methods=['POST'])
@login_required
def delete_category(cat_id):
    cat = Category.query.get_or_404(cat_id)

    # prevent deletion if cat is in use
    if cat.expenses:
        flash("Cannot delete a category that is in use.", "error")
        return redirect(url_for('edit_category', cat_id=cat.id))

    db.session.delete(cat)
    db.session.commit()
    flash("Category deleted!", "success")
    return redirect(url_for('manage_categories', cat_id=cat.id))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/add', methods=['POST'])
@login_required
def add_expense():
    description = (request.form.get('description') or '').strip()
    amount = (request.form.get('amount') or '').strip()
    category_name = (request.form.get('category') or '').strip()
    date_exp = (request.form.get('date') or '').strip()

    # Validate amount
    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("Amount must be a positive number", "error")
        return redirect(url_for('dashboard'))

    # Validate date
    try:
        date_exp = datetime.strptime(date_exp, '%Y-%m-%d').date() if date_exp else date.today()
    except ValueError:
        date_exp = date.today()

    # Validate description and category
    if not description or not category_name:
        flash('Invalid expense data. Please try again.', 'error')
        return redirect(url_for('dashboard'))

    category = Category.query.filter_by(name=category_name, user_id=current_user.id).first()
    if not category:
        flash("Invalid category", "error")
        return redirect(url_for('dashboard'))
    # Create new expense
    new_expense = Expense(
        description=description,
        amount=amount,
        category_id=category.id,
        date=date_exp,
        user_id=current_user.id
    )

    db.session.add(new_expense)
    db.session.commit()
    flash('Expense added successfully!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
	expense = Expense.query.get_or_404(expense_id)
	db.session.delete(expense)
	db.session.commit()
	flash('Expense deleted successfully!', 'success')
	return redirect(url_for('dashboard'))

@app.route('/edit/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)

    if request.method == 'POST':

        description = (request.form.get('description') or expense.description).strip()

        # handle category name to category object
        category_name = (request.form.get('category') or expense.category.name).strip()
        category_obj = Category.query.filter_by(name=category_name, user_id=current_user.id).first()
        if not category_obj:
            flash("Invalid category.", "error")
            return redirect(url_for('edit_expense', expense_id=expense.id))

        # Amount
        amount_raw = request.form.get('amount')
        if amount_raw:
            try:
                amount = float(amount_raw)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                flash("Amount must be positive", "error")
                return redirect(url_for('edit_expense', expense_id=expense.id))
        else:
            amount = expense.amount

        # Date
        date_raw = request.form.get('date')
        if date_raw:
            try:
                date_exp = datetime.strptime(date_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid date.", "error")
                return redirect(url_for('edit_expense', expense_id=expense.id))
        else:
            date_exp = expense.date

        # update fields
        expense.description = description
        expense.amount = amount
        expense.date = date_exp
        expense.category_id = category_obj.id

        db.session.commit()
        flash("Expense updated successfully!", "success")
        return redirect(url_for('dashboard'))

    # GET request
    return render_template(
        'edit_expense.html',
        e=expense,
        categories = Category.query.filter_by(user_id=current_user.id).all(),
        today=date.today().isoformat()
    )


@app.route('/export.csv')
@login_required
def export_csv():
    start_str = (request.args.get('start') or '').strip()
    end_str = (request.args.get('end') or '').strip()
    start_date = parse_date(start_str)
    end_date = parse_date(end_str)
    
    q = Expense.query.filter_by(user_id=current_user.id)

    if start_date:
        q = q.filter(Expense.date >= start_date)
    if end_date:
        q = q.filter(Expense.date <= end_date)

    min_amount = (request.args.get('min_amount') or '').strip()
    max_amount = (request.args.get('max_amount') or '').strip()

    if min_amount:
        q = q.filter(Expense.amount >= float(min_amount))
    if max_amount:
        q = q.filter(Expense.amount <= float(max_amount))

    filter_category = (request.args.get('filter_category') or '').strip()
    if filter_category:
        q = q.filter(Category.name == filter_category)
 

    expenses = q.order_by(Expense.date.desc()).all()
    
    lines = ['date,description,category,amount']
    
    for e in expenses:
        lines.append(f"{e.date.isoformat()}, {e.description}, {e.category.name}, {e.amount:.2f}")
    csv_data = '\n'.join(lines)
    
    fname_start = start_str or 'all'
    fname_end = end_str or 'all'
    filename = f"expenses_{fname_start}_to_{fname_end}.csv"
    
    return Response(
        csv_data,
        headers={
            "Content-Type": "text/csv",
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )

def init_categories_for_user(user_id):
    default_categories = ["Food", "Rent", "Utilities", "Entertainment", "Transportation", "Subscriptions"]

    for cat_name in default_categories:
        exists = Category.query.filter_by(name=cat_name, user_id=user_id).first()
        if not exists:
            new_cat = Category(name=cat_name, user_id=user_id)
            db.session.add(new_cat)
    db.session.commit()

    
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
