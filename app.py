import os
import io # <--- PENTING: Library untuk file memory
import json
import secrets
from datetime import datetime
from functools import wraps
import pandas as pd
from flask import Flask, render_template, request, send_file, flash, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from fpdf import FPDF

# Import Modul GA
from app.genetic_algorithm.population import Population

def _load_env_file(path='.env'):
    """Muat environment variable sederhana dari file .env jika tersedia."""
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

_load_env_file()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'kunci_rahasia_project_skripsi')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URI', 'mysql+pymysql://root:@localhost/smart_scheduler_db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- GLOBAL CACHE ---
LAST_RESULT = {'schedule': None, 'fitness': 0, 'conflicts': 0, 'logs': [], 'fitness_history': []}

# --- DATABASE MODELS ---
class MataKuliah(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kode_mk = db.Column(db.String(50))
    nama_mk = db.Column(db.String(150))
    sks = db.Column(db.Integer)
    fakultas = db.Column(db.String(100))
    dosen = db.Column(db.String(150))
    waktu_tersedia = db.Column(db.String(255))
    jumlah_mhs = db.Column(db.Integer)
    jenis_mk = db.Column(db.String(50))
    semester = db.Column(db.Integer, default=1)

class Ruangan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama_ruangan = db.Column(db.String(50))
    kapasitas = db.Column(db.Integer)
    jenis = db.Column(db.String(50))
    fasilitas = db.Column(db.String(255), default="Standard")

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    nama_lengkap = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')

class ScheduleRun(db.Model):
    __tablename__ = 'schedule_runs'
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    created_by = db.Column(db.String(80), default='admin')
    fitness_score = db.Column(db.Integer, default=0)
    conflicts = db.Column(db.Integer, default=0)
    population_size = db.Column(db.Integer, default=150)
    max_generations = db.Column(db.Integer, default=200)
    mutation_rate = db.Column(db.Float, default=0.02)
    tournament_size = db.Column(db.Integer, default=4)
    fitness_history_json = db.Column(db.Text, default='[]')
    logs_json = db.Column(db.Text, default='[]')
    entries = db.relationship('ScheduleEntry', backref='run', cascade='all, delete-orphan', lazy=True)

class ScheduleEntry(db.Model):
    __tablename__ = 'schedules'
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('schedule_runs.id', ondelete='CASCADE'), nullable=False)
    hari = db.Column(db.String(30))
    jam = db.Column(db.String(40))
    nama_ruangan = db.Column(db.String(50))
    kapasitas_ruangan = db.Column(db.Integer)
    nama_mk = db.Column(db.String(150))
    kode_mk = db.Column(db.String(50))
    dosen = db.Column(db.String(150))
    fakultas = db.Column(db.String(100))
    sks = db.Column(db.Integer)
    jumlah_mhs = db.Column(db.Integer)
    jenis_mk = db.Column(db.String(50))
    assigned_time = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

_db_initialized = False

def generate_csrf_token():
    """Buat dan kembalikan token CSRF untuk sesi aktif."""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = generate_csrf_token

def seed_default_users():
    """Buat akun default admin dan dosen jika belum ada."""
    admin_pass = os.environ.get('DEFAULT_ADMIN_PASS', 'admin123')
    user_pass = os.environ.get('DEFAULT_USER_PASS', 'user123')
    dosen_pass = os.environ.get('DEFAULT_DOSEN_PASS', 'dosen123')
    defaults = [
        ('admin', admin_pass, 'Administrator', 'admin'),
        ('dosen', dosen_pass, 'Dosen Pengampu', 'user'),
        ('user', user_pass, 'Dosen Pengampu', 'user'),
    ]
    created = False
    for username, raw_password, nama_lengkap, role in defaults:
        existing = User.query.filter_by(username=username).first()
        if not existing:
            db.session.add(User(
                username=username,
                password_hash=generate_password_hash(raw_password),
                nama_lengkap=nama_lengkap,
                role=role
            ))
            created = True
        else:
            if existing.nama_lengkap != nama_lengkap:
                existing.nama_lengkap = nama_lengkap
                created = True
    if created:
        db.session.commit()

def ensure_db_ready():
    """Pastikan tabel database dan akun default tersedia."""
    global _db_initialized
    if not _db_initialized:
        db.create_all()
        seed_default_users()
        _db_initialized = True

@app.before_request
def _init_and_protect_requests():
    ensure_db_ready()
    if request.method == 'POST':
        session_token = session.get('_csrf_token', '')
        form_token = request.form.get('csrf_token', '')
        if not session_token or not form_token or not secrets.compare_digest(session_token, form_token):
            flash('Token keamanan (CSRF) tidak valid atau sesi kedaluwarsa. Silakan ulangi.', 'error')
            return redirect(request.referrer or url_for('login'))

def login_required(f):
    """Wajibkan sesi login aktif sebelum mengakses route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Silakan login terlebih dahulu!', 'error')
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Batasi akses route hanya untuk role admin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Silakan login terlebih dahulu!', 'error')
            return redirect(url_for('login', next=request.path))
        if session.get('role') != 'admin':
            flash('Akses ditolak! Halaman ini khusus Admin.', 'error')
            return redirect(url_for('hasil_jadwal'))
        return f(*args, **kwargs)
    return decorated_function

# --- ADAPTER CLASSES ---
class ObjDosen:
    def __init__(self, id, nama): self.id = id; self.nama = nama

class ObjRuangan:
    def __init__(self, id, nama, kapasitas, jenis): 
        self.id = id; self.nama = nama; self.kapasitas = kapasitas; self.jenis = jenis

class ObjMatkul:
    def __init__(self, id, kode, nama, sks, semester, jenis, mhs, dosen_obj, fakultas):
        self.id = id; self.kode = kode; self.nama = nama; self.sks = sks
        self.semester = semester; self.jenis = jenis; self.jumlah_mhs = mhs
        self.dosen_obj = dosen_obj
        self.fakultas = fakultas 

# --- HELPER TIME
def get_readable_time(slot_id):
    try:
        slot = int(slot_id) - 1
        days = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat"]
        
        # Update jam agar sesuai dengan Tabel Panduan di website
        times = [
            "07:00 - 08:40",  # Slot 1
            "08:40 - 10:20",  # Slot 2
            "10:20 - 12:00",  # Slot 3
            "13:00 - 14:40",  # Slot 4 (Istirahat Siang 12:00-13:00)
            "14:40 - 16:20",  # Slot 5
            "16:20 - 18:00",  # Slot 6 (Istirahat Sore/Maghrib 18:00-18:30)
            "18:30 - 20:10",  # Slot 7
            "20:10 - 21:50"   # Slot 8
        ]
        
        day_idx = slot // 8
        time_idx = slot % 8
        
        if day_idx < len(days) and time_idx < len(times):
            return days[day_idx], times[time_idx]
        return "Weekend", "Extra"
    except: return "-", "-"

# ================= ROUTES =================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index') if session.get('role') == 'admin' else url_for('hasil_jadwal'))

    selected_role = request.args.get('role', 'admin')
    if selected_role not in ('admin', 'user'):
        selected_role = 'admin'

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        selected_role = request.form.get('role', 'admin')
        if selected_role not in ('admin', 'user'):
            selected_role = 'admin'

        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash('Username atau password salah!', 'error')
            return render_template('login.html', active_role=selected_role, entered_username=username)

        if user.role != selected_role:
            role_label = 'ADMIN' if user.role == 'admin' else 'DOSEN'
            flash(f'Akun "{username}" terdaftar sebagai {role_label}. Pilih tab LOGIN {role_label} untuk masuk.', 'error')
            return render_template('login.html', active_role=selected_role, entered_username=username)

        session['user_id'] = user.id
        session['username'] = user.username
        session['nama_lengkap'] = user.nama_lengkap
        session['role'] = user.role
        flash(f'Login berhasil! Selamat datang, {user.nama_lengkap}.', 'success')

        next_url = request.args.get('next', '')
        if next_url and next_url.startswith('/') and not next_url.startswith('//'):
            if user.role == 'admin' or next_url in ('/hasil', '/dosen'):
                return redirect(next_url)

        return redirect(url_for('index') if user.role == 'admin' else url_for('hasil_jadwal'))

    return render_template('login.html', active_role=selected_role, entered_username='')

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    flash('Anda telah keluar dari sistem.', 'success')
    return redirect(url_for('login'))

def parse_available_slots(raw_avail):
    """Parse string slot tersedia (mis. '1;2;3') menjadi set integer."""
    if not raw_avail or str(raw_avail).strip().lower() in ('nan', 'none', ''):
        return set()
    slots = set()
    for token in str(raw_avail).replace(',', ';').split(';'):
        token = token.strip()
        if token.isdigit():
            val = int(token)
            if 1 <= val <= 40:
                slots.add(val)
    return slots

def _clamp_int(raw_val, default, min_val, max_val):
    try:
        return max(min_val, min(max_val, int(raw_val)))
    except (TypeError, ValueError):
        return default

def _clamp_float(raw_val, default, min_val, max_val):
    try:
        return max(min_val, min(max_val, float(raw_val)))
    except (TypeError, ValueError):
        return default

def load_schedule_run(run_id=None):
    """Muat hasil penjadwalan tersimpan dari basis data."""
    run = None
    if run_id is not None:
        run = db.session.get(ScheduleRun, run_id)
    if run is None:
        run = ScheduleRun.query.order_by(ScheduleRun.id.desc()).first()
    if run is None:
        return None

    formatted = [
        {
            'Hari': entry.hari,
            'Jam': entry.jam,
            'Nama_Ruangan': entry.nama_ruangan,
            'Kapasitas_Ruangan': entry.kapasitas_ruangan,
            'Nama_MK': entry.nama_mk,
            'Kode_MK': entry.kode_mk,
            'Dosen': entry.dosen,
            'Fakultas': entry.fakultas,
            'SKS': entry.sks,
            'Jumlah_Mhs': entry.jumlah_mhs,
            'Jenis_MK': entry.jenis_mk,
            'assigned_time': entry.assigned_time,
        }
        for entry in sorted(run.entries, key=lambda e: e.assigned_time or 0)
    ]
    try:
        logs = json.loads(run.logs_json or '[]')
    except ValueError:
        logs = []
    try:
        fitness_history = json.loads(run.fitness_history_json or '[]')
    except ValueError:
        fitness_history = []

    return {
        'run': run,
        'schedule': formatted,
        'fitness': run.fitness_score,
        'conflicts': run.conflicts,
        'logs': logs,
        'fitness_history': fitness_history,
    }

@app.route('/', methods=['GET', 'POST'])
@admin_required
def index():
    if request.method == 'POST':
        upload_type = request.form.get('upload_type')
        file = None

        if upload_type == 'excel':
            file = request.files.get('file_excel')
        elif upload_type in ['csv_matkul', 'csv_ruangan']:
            file = request.files.get('file_csv')

        if file and file.filename != '':
            try:
                # --- KASUS 1: EXCEL ---
                if upload_type == 'excel' and file.filename.endswith('.xlsx'):
                    xls = pd.ExcelFile(file, engine='openpyxl')
                    
                    if 'Matkul' in xls.sheet_names:
                        df_mk = pd.read_excel(xls, 'Matkul')
                        MataKuliah.query.delete()
                        for _, row in df_mk.iterrows():
                            db.session.add(MataKuliah(
                                kode_mk=row['Kode_MK'], nama_mk=row['Nama_MK'], sks=int(row['SKS']),
                                dosen=row['Dosen'], waktu_tersedia=str(row['Waktu_Tersedia']),
                                jumlah_mhs=int(row.get('Jumlah_Mhs', 40)), jenis_mk=row.get('Jenis_MK', 'Teori'),
                                semester=int(row.get('Semester', 1)), fakultas=row.get('Fakultas', '-')
                            ))
                    else: flash('Gagal: Sheet "Matkul" hilang!', 'error')

                    if 'Ruangan' in xls.sheet_names:
                        df_ru = pd.read_excel(xls, 'Ruangan')
                        Ruangan.query.delete()
                        for _, row in df_ru.iterrows():
                            db.session.add(Ruangan(
                                nama_ruangan=row['Nama_Ruangan'], kapasitas=int(row['Kapasitas']), 
                                jenis=row['Jenis']
                            ))
                    else: flash('Gagal: Sheet "Ruangan" hilang!', 'error')
                    
                    db.session.commit()
                    flash('Sukses Import Excel!', 'success')

                # --- KASUS 2: CSV MATKUL ---
                elif upload_type == 'csv_matkul' and file.filename.endswith('.csv'):
                    df = pd.read_csv(file, sep=None, engine='python')
                    df.columns = df.columns.str.strip()
                    MataKuliah.query.delete()
                    for _, row in df.iterrows():
                        db.session.add(MataKuliah(
                            kode_mk=row['Kode_MK'], nama_mk=row['Nama_MK'], sks=int(row['SKS']),
                            dosen=row['Dosen'], waktu_tersedia=str(row['Waktu_Tersedia']),
                            jumlah_mhs=int(row.get('Jumlah_Mhs', 40)), jenis_mk=row.get('Jenis_MK', 'Teori'),
                            semester=int(row.get('Semester', 1)), fakultas=row.get('Fakultas', '-')
                        ))
                    db.session.commit()
                    flash('Sukses Import CSV Matkul!', 'success')

                # --- KASUS 3: CSV RUANGAN ---
                elif upload_type == 'csv_ruangan' and file.filename.endswith('.csv'):
                    df = pd.read_csv(file, sep=None, engine='python')
                    df.columns = df.columns.str.strip()
                    Ruangan.query.delete()
                    for _, row in df.iterrows():
                        db.session.add(Ruangan(
                            nama_ruangan=row['Nama_Ruangan'], kapasitas=int(row['Kapasitas']), 
                            jenis=row['Jenis']
                        ))
                    db.session.commit()
                    flash('Sukses Import CSV Ruangan!', 'success')
                else:
                    flash('Format file salah! Gunakan .xlsx atau .csv.', 'error')

            except Exception as e:
                flash(f'Error: {str(e)}', 'error')

        return redirect('/')

    data_mk = MataKuliah.query.all()
    data_ruangan = Ruangan.query.all()
    stats = {'sks': sum([mk.sks for mk in data_mk]), 'dosen': len(set([mk.dosen for mk in data_mk])), 'ruangan': len(data_ruangan)}
    return render_template('index.html', data=data_mk, rooms=data_ruangan, stats=stats)

@app.route('/run_ga', methods=['POST'])
@admin_required
def run_ga():
    db_matkul = MataKuliah.query.all()
    db_ruangan = Ruangan.query.all()

    if not db_matkul or not db_ruangan:
        flash("Data Kosong! Upload atau tambah data terlebih dahulu.", 'error')
        return redirect('/')

    pop_size = _clamp_int(request.form.get('population_size'), 150, 10, 500)
    max_gens = _clamp_int(request.form.get('max_generations'), 200, 10, 1000)
    mut_rate = _clamp_float(request.form.get('mutation_rate'), 0.02, 0.005, 0.5)
    tourn_size = _clamp_int(request.form.get('tournament_size'), 4, 2, 10)

    dosen_map, dosen_objs, matkul_objs, ruangan_objs, preferensi_dict = {}, [], [], [], {}

    for r in db_ruangan:
        ruangan_objs.append(ObjRuangan(r.id, r.nama_ruangan, r.kapasitas, r.jenis))
    
    for mk in db_matkul:
        if mk.dosen not in dosen_map:
            d_obj = ObjDosen(len(dosen_map)+1, mk.dosen)
            dosen_map[mk.dosen] = d_obj
            dosen_objs.append(d_obj)
        
        m_obj = ObjMatkul(
            mk.id, mk.kode_mk, mk.nama_mk, mk.sks, mk.semester, 
            mk.jenis_mk, mk.jumlah_mhs, dosen_map[mk.dosen], mk.fakultas
        )
        matkul_objs.append(m_obj)
        allowed_slots = parse_available_slots(mk.waktu_tersedia)
        if allowed_slots:
            preferensi_dict[mk.id] = allowed_slots

    try:
        population = Population(
            matkul_objs, dosen_objs, ruangan_objs, preferensi_dict,
            population_size=pop_size, tournament_size=tourn_size
        )
        best_chrom, final_score, final_conflicts, final_logs = population.run_genetic_algorithm(
            max_generations=max_gens, mutation_rate=mut_rate
        )
        fitness_history = getattr(population, 'fitness_history', [])

        formatted_schedule = []
        map_mk = {m.id: m for m in matkul_objs}
        map_ru = {r.id: r for r in ruangan_objs}
        map_ds = {d.id: d for d in dosen_objs}

        for gene in best_chrom.genes:
            mk = map_mk[gene.matkul_id]
            ru = map_ru[gene.ruangan_id]
            ds = map_ds[gene.dosen_id]
            hari, jam = get_readable_time(gene.time_slot)

            formatted_schedule.append({
                'Hari': hari, 'Jam': jam, 'Nama_Ruangan': ru.nama,
                'Kapasitas_Ruangan': ru.kapasitas, 'Nama_MK': mk.nama,
                'Kode_MK': mk.kode, 'Dosen': ds.nama, 'Fakultas': mk.fakultas,
                'SKS': mk.sks, 'Jumlah_Mhs': mk.jumlah_mhs, 'Jenis_MK': mk.jenis,
                'assigned_time': gene.time_slot
            })

        # Simpan permanen ke database (FR-02)
        run_record = ScheduleRun(
            created_by=session.get('username', 'admin'),
            fitness_score=int(round(final_score)),
            conflicts=int(final_conflicts),
            population_size=pop_size,
            max_generations=max_gens,
            mutation_rate=mut_rate,
            tournament_size=tourn_size,
            fitness_history_json=json.dumps(fitness_history),
            logs_json=json.dumps(final_logs),
        )
        db.session.add(run_record)
        db.session.flush()

        for row in formatted_schedule:
            db.session.add(ScheduleEntry(
                run_id=run_record.id,
                hari=row['Hari'],
                jam=row['Jam'],
                nama_ruangan=row['Nama_Ruangan'],
                kapasitas_ruangan=row['Kapasitas_Ruangan'],
                nama_mk=row['Nama_MK'],
                kode_mk=row['Kode_MK'],
                dosen=row['Dosen'],
                fakultas=row['Fakultas'],
                sks=row['SKS'],
                jumlah_mhs=row['Jumlah_Mhs'],
                jenis_mk=row['Jenis_MK'],
                assigned_time=row['assigned_time'],
            ))
        db.session.commit()

        global LAST_RESULT
        LAST_RESULT = {
            'schedule': formatted_schedule,
            'fitness': final_score,
            'conflicts': final_conflicts,
            'logs': final_logs,
            'fitness_history': fitness_history,
        }

        create_pdf_report(formatted_schedule)
        flash(f"Optimasi selesai & jadwal #{run_record.id} tersimpan permanen!", 'success')
        return redirect(url_for('hasil_jadwal', run_id=run_record.id))
    
    except Exception as e:
        db.session.rollback()
        flash(f"Error Algoritma: {str(e)}", 'error')
        import traceback; traceback.print_exc()
        return redirect('/')

@app.route('/hasil')
@login_required
def hasil_jadwal():
    run_id = request.args.get('run_id', type=int)
    loaded = load_schedule_run(run_id)
    runs = ScheduleRun.query.order_by(ScheduleRun.id.desc()).limit(15).all()

    if loaded is not None:
        return render_template(
            'result.html',
            schedule=loaded['schedule'],
            fitness=loaded['fitness'],
            conflicts=loaded['conflicts'],
            logs=loaded['logs'],
            fitness_history=loaded['fitness_history'],
            active_run=loaded['run'],
            runs=runs,
        )

    if LAST_RESULT['schedule'] is not None:
        return render_template(
            'result.html',
            schedule=LAST_RESULT['schedule'],
            fitness=LAST_RESULT['fitness'],
            conflicts=LAST_RESULT['conflicts'],
            logs=LAST_RESULT['logs'],
            fitness_history=LAST_RESULT.get('fitness_history', []),
            active_run=None,
            runs=runs,
        )

    return render_template(
        'result.html',
        schedule=None,
        fitness=0,
        conflicts=0,
        logs=[],
        fitness_history=[],
        active_run=None,
        runs=runs,
    )

@app.route('/dosen')
@login_required
def data_dosen():
    lecturers = {}
    for mk in MataKuliah.query.all():
        if mk.dosen not in lecturers: 
            lecturers[mk.dosen] = {'nama': mk.dosen, 'total_sks': 0, 'total_kelas': 0, 'daftar_matkul': []}
        lecturers[mk.dosen]['total_sks'] += mk.sks
        lecturers[mk.dosen]['total_kelas'] += 1
        lecturers[mk.dosen]['daftar_matkul'].append(f"{mk.nama_mk} ({mk.sks} SKS)")
    
    final_list = sorted(list(lecturers.values()), key=lambda x: x['total_sks'], reverse=True)
    return render_template('dosen.html', lecturers=final_list)

# --- CRUD DATA MASTER INDIVIDUAL (FR-10, ADMIN ONLY) ---
@app.route('/matkul/add', methods=['POST'])
@admin_required
def add_matkul():
    try:
        kode_mk = request.form.get('kode_mk', '').strip()
        nama_mk = request.form.get('nama_mk', '').strip()
        dosen = request.form.get('dosen', '').strip()
        if not kode_mk or not nama_mk or not dosen:
            flash('Kode MK, Nama MK, dan Dosen wajib diisi!', 'error')
            return redirect('/')
        sks = _clamp_int(request.form.get('sks'), 3, 1, 8)
        jumlah_mhs = _clamp_int(request.form.get('jumlah_mhs'), 40, 1, 500)
        semester = _clamp_int(request.form.get('semester'), 1, 1, 14)
        jenis_mk = request.form.get('jenis_mk', 'Teori')
        if jenis_mk not in ('Teori', 'Laboratorium'):
            jenis_mk = 'Teori'
        fakultas = request.form.get('fakultas', '-').strip() or '-'
        waktu_tersedia = request.form.get('waktu_tersedia', '').strip() or ';'.join(str(i) for i in range(1, 41))

        db.session.add(MataKuliah(
            kode_mk=kode_mk, nama_mk=nama_mk, sks=sks, fakultas=fakultas,
            dosen=dosen, waktu_tersedia=waktu_tersedia, jumlah_mhs=jumlah_mhs,
            jenis_mk=jenis_mk, semester=semester
        ))
        db.session.commit()
        flash(f'Mata kuliah "{nama_mk}" berhasil ditambahkan!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Gagal menambah mata kuliah: {str(e)}', 'error')
    return redirect('/')

@app.route('/matkul/edit/<int:id>', methods=['POST'])
@admin_required
def edit_matkul(id):
    mk = db.session.get(MataKuliah, id)
    if not mk:
        flash('Data mata kuliah tidak ditemukan.', 'error')
        return redirect('/')
    try:
        mk.kode_mk = request.form.get('kode_mk', mk.kode_mk).strip() or mk.kode_mk
        mk.nama_mk = request.form.get('nama_mk', mk.nama_mk).strip() or mk.nama_mk
        mk.dosen = request.form.get('dosen', mk.dosen).strip() or mk.dosen
        mk.sks = _clamp_int(request.form.get('sks'), mk.sks, 1, 8)
        mk.jumlah_mhs = _clamp_int(request.form.get('jumlah_mhs'), mk.jumlah_mhs, 1, 500)
        mk.semester = _clamp_int(request.form.get('semester'), mk.semester, 1, 14)
        jenis_mk = request.form.get('jenis_mk', mk.jenis_mk)
        mk.jenis_mk = jenis_mk if jenis_mk in ('Teori', 'Laboratorium') else 'Teori'
        mk.fakultas = request.form.get('fakultas', mk.fakultas).strip() or '-'
        mk.waktu_tersedia = request.form.get('waktu_tersedia', mk.waktu_tersedia).strip() or ';'.join(str(i) for i in range(1, 41))
        db.session.commit()
        flash(f'Mata kuliah "{mk.nama_mk}" berhasil diperbarui!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Gagal mengubah mata kuliah: {str(e)}', 'error')
    return redirect('/')

@app.route('/matkul/delete/<int:id>', methods=['POST'])
@admin_required
def delete_matkul(id):
    mk = db.session.get(MataKuliah, id)
    if mk:
        nama = mk.nama_mk
        db.session.delete(mk)
        db.session.commit()
        flash(f'Mata kuliah "{nama}" berhasil dihapus!', 'success')
    return redirect('/')

@app.route('/ruangan/add', methods=['POST'])
@admin_required
def add_ruangan():
    try:
        nama_ruangan = request.form.get('nama_ruangan', '').strip()
        if not nama_ruangan:
            flash('Nama ruangan wajib diisi!', 'error')
            return redirect('/')
        kapasitas = _clamp_int(request.form.get('kapasitas'), 40, 1, 1000)
        jenis = request.form.get('jenis', 'Teori')
        if jenis not in ('Teori', 'Laboratorium'):
            jenis = 'Teori'
        fasilitas = request.form.get('fasilitas', 'Standard').strip() or 'Standard'

        db.session.add(Ruangan(
            nama_ruangan=nama_ruangan, kapasitas=kapasitas, jenis=jenis, fasilitas=fasilitas
        ))
        db.session.commit()
        flash(f'Ruangan "{nama_ruangan}" berhasil ditambahkan!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Gagal menambah ruangan: {str(e)}', 'error')
    return redirect('/')

@app.route('/ruangan/edit/<int:id>', methods=['POST'])
@admin_required
def edit_ruangan(id):
    ruangan = db.session.get(Ruangan, id)
    if not ruangan:
        flash('Data ruangan tidak ditemukan.', 'error')
        return redirect('/')
    try:
        ruangan.nama_ruangan = request.form.get('nama_ruangan', ruangan.nama_ruangan).strip() or ruangan.nama_ruangan
        ruangan.kapasitas = _clamp_int(request.form.get('kapasitas'), ruangan.kapasitas, 1, 1000)
        jenis = request.form.get('jenis', ruangan.jenis)
        ruangan.jenis = jenis if jenis in ('Teori', 'Laboratorium') else 'Teori'
        ruangan.fasilitas = request.form.get('fasilitas', ruangan.fasilitas).strip() or 'Standard'
        db.session.commit()
        flash(f'Ruangan "{ruangan.nama_ruangan}" berhasil diperbarui!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Gagal mengubah ruangan: {str(e)}', 'error')
    return redirect('/')

@app.route('/schedule/delete/<int:run_id>', methods=['POST'])
@admin_required
def delete_schedule_run(run_id):
    run = db.session.get(ScheduleRun, run_id)
    if run:
        db.session.delete(run)
        db.session.commit()
        flash(f'Riwayat jadwal #{run_id} dihapus.', 'success')
    return redirect(url_for('hasil_jadwal'))

# --- FITUR BARU: DOWNLOAD TEMPLATE ---
@app.route('/download_template/<file_type>')
@admin_required
def download_template(file_type):
    # Header Standar
    headers_matkul = {
        'Kode_MK': ['CS101'], 'Nama_MK': ['Algoritma'], 'SKS': [3], 'Fakultas': ['Ilkom'], 
        'Dosen': ['Dr. Budi'], 'Waktu_Tersedia': ['1;2;3;4;5'], 
        'Jumlah_Mhs': [40], 'Jenis_MK': ['Teori'], 'Semester': [1]
    }
    headers_ruangan = {
        'Nama_Ruangan': ['R.101'], 'Kapasitas': [40], 'Jenis': ['Teori']
    }

    buffer = io.BytesIO()
    try:
        if file_type == 'excel':
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                pd.DataFrame(headers_matkul).to_excel(writer, sheet_name='Matkul', index=False)
                pd.DataFrame(headers_ruangan).to_excel(writer, sheet_name='Ruangan', index=False)
            filename, mimetype = 'Template_Lengkap.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        
        elif file_type == 'csv_matkul':
            pd.DataFrame(headers_matkul).to_csv(buffer, index=False, sep=',')
            filename, mimetype = 'Template_Matkul.csv', 'text/csv'
            
        elif file_type == 'csv_ruangan':
            pd.DataFrame(headers_ruangan).to_csv(buffer, index=False, sep=',')
            filename, mimetype = 'Template_Ruangan.csv', 'text/csv'
            
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype=mimetype)
    except Exception as e:
        return str(e), 500

@app.route('/download_pdf')
@login_required
def download_pdf():
    run_id = request.args.get('run_id', type=int)
    loaded = load_schedule_run(run_id)
    if loaded is not None:
        create_pdf_report(loaded['schedule'])
        return send_file('static/schedule_report.pdf', as_attachment=True)
    if LAST_RESULT['schedule'] is not None:
        create_pdf_report(LAST_RESULT['schedule'])
        return send_file('static/schedule_report.pdf', as_attachment=True)
    flash('Belum ada jadwal yang dapat dicetak ke PDF.', 'error')
    return redirect(url_for('hasil_jadwal'))

@app.route('/reset_db', methods=['POST'])
@admin_required
def reset_db():
    MataKuliah.query.delete(); Ruangan.query.delete(); db.session.commit()
    flash('Semua data Matkul & Ruangan berhasil dikosongkan.', 'success')
    return redirect('/')

@app.route('/delete_room/<int:id>', methods=['POST'])
@admin_required
def delete_room(id):
    Ruangan.query.filter_by(id=id).delete(); db.session.commit()
    flash('Ruangan berhasil dihapus.', 'success')
    return redirect('/')

def create_pdf_report(data):
    os.makedirs('static', exist_ok=True)
    pdf = FPDF()
    pdf.add_page(); pdf.set_font("Arial", 'B', 14); pdf.cell(0, 10, "Laporan Jadwal Kuliah", 0, 1, 'C'); pdf.ln(10)
    pdf.set_font("Arial", size=8)
    pdf.cell(20, 10, "Hari", 1); pdf.cell(20, 10, "Jam", 1); pdf.cell(30, 10, "Ruang", 1); 
    pdf.cell(50, 10, "Matkul", 1); pdf.cell(40, 10, "Dosen", 1); pdf.cell(30, 10, "Fakultas", 1); pdf.ln()
    data.sort(key=lambda x: x['assigned_time'])
    for row in data:
        pdf.cell(20, 10, str(row['Hari']), 1); pdf.cell(20, 10, str(row['Jam']), 1)
        pdf.cell(30, 10, str(row['Nama_Ruangan'])[:18], 1); pdf.cell(50, 10, str(row['Nama_MK'])[:30], 1)
        pdf.cell(40, 10, str(row['Dosen'])[:25], 1); pdf.cell(30, 10, str(row['Fakultas'])[:18], 1); pdf.ln()
    pdf.output("static/schedule_report.pdf")

if __name__ == '__main__':
    with app.app_context():
        ensure_db_ready()
    app.run(debug=True, port=5001) # Ubah port di sini
