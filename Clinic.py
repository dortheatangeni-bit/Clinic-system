import sqlite3
import hashlib
import os
import csv
import shutil
from datetime import datetime, date, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clinic.db")


def connect(): 
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables():
    conn = connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS user_account (
        user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS patient (
        patient_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        national_id TEXT NOT NULL UNIQUE,
        first_name  TEXT NOT NULL,
        last_name   TEXT NOT NULL,
        birth_date  TEXT,
        phone       TEXT NOT NULL,
        address     TEXT
    );
    CREATE TABLE IF NOT EXISTS doctor (
        doctor_id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        specialty TEXT
    );
    CREATE TABLE IF NOT EXISTS doctor_availability (
        availability_id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id    INTEGER NOT NULL REFERENCES doctor(doctor_id),
        day_of_week  INTEGER NOT NULL,
        start_time   TEXT NOT NULL,
        end_time     TEXT NOT NULL,
        slot_minutes INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS appointment (
        appointment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL REFERENCES patient(patient_id),
        doctor_id  INTEGER NOT NULL REFERENCES doctor(doctor_id),
        appt_date  TEXT NOT NULL,
        appt_time  TEXT NOT NULL,
        reason     TEXT,
        status     TEXT NOT NULL DEFAULT 'Booked'
    );
    CREATE UNIQUE INDEX IF NOT EXISTS no_double_booking
        ON appointment (doctor_id, appt_date, appt_time) WHERE status = 'Booked';
    CREATE TABLE IF NOT EXISTS visit (
        visit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        appointment_id INTEGER NOT NULL UNIQUE REFERENCES appointment(appointment_id),
        complaint      TEXT NOT NULL,
        treatment      TEXT,
        notes          TEXT,
        follow_up_date TEXT
    );
    """)
    conn.commit()
    conn.close()


def hash_password(password, salt=None):
    salt = salt or os.urandom(8).hex()
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return salt + "$" + digest


def check_password(password, stored):
    salt = stored.split("$")[0]
    return hash_password(password, salt) == stored


def add_sample_data():
    conn = connect()
    if conn.execute("SELECT COUNT(*) FROM user_account").fetchone()[0] == 0:
        for username, password, role in [
            ("admin", "admin123", "admin"),
            ("reception", "reception123", "receptionist"),
            ("doctor", "doctor123", "doctor"),
        ]:
            conn.execute(
                "INSERT INTO user_account (username, password_hash, role) VALUES (?,?,?)",
                (username, hash_password(password), role),
            )
        for name, specialty in [("Dr A. Smith", "General"), ("Dr B. Jones", "General")]:
            cur = conn.execute(
                "INSERT INTO doctor (full_name, specialty) VALUES (?,?)", (name, specialty)
            )
            for weekday in range(1, 6):
                conn.execute(
                    "INSERT INTO doctor_availability (doctor_id, day_of_week, start_time, "
                    "end_time, slot_minutes) VALUES (?,?,?,?,?)",
                    (cur.lastrowid, weekday, "08:00", "12:00", 30),
                )
        conn.commit()
    conn.close()


PERMISSIONS = {
    "register_patient": ["admin", "receptionist"],
    "book": ["admin", "receptionist"],
    "record_visit": ["doctor", "nurse"],
    "backup": ["admin"],
}


def can_do(role, action):
    return role in PERMISSIONS.get(action, [])


def login(username, password):
    conn = connect()
    row = conn.execute("SELECT * FROM user_account WHERE username = ?", (username,)).fetchone()
    conn.close()
    if row is None or not check_password(password, row["password_hash"]):
        return None, "Invalid login"
    return dict(row), "Login successful"


def register_patient(national_id, first, last, birth, phone, address):
    if not national_id or not first or not last or not phone:
        return False, "Please fill in ID number, first name, last name and phone."
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO patient (national_id, first_name, last_name, birth_date, phone, address) "
            "VALUES (?,?,?,?,?,?)",
            (national_id, first, last, birth, phone, address),
        )
        conn.commit()
        return True, "Patient saved."
    except sqlite3.IntegrityError:
        return False, "A patient with this ID number is already registered."
    finally:
        conn.close()


def find_patients(term):
    conn = connect()
    like = "%" + term + "%"
    rows = conn.execute(
        "SELECT * FROM patient WHERE first_name LIKE ? OR last_name LIKE ? "
        "OR national_id LIKE ? OR phone LIKE ? ORDER BY last_name LIMIT 200",
        (like, like, like, like),
    ).fetchall()
    conn.close()
    return rows


def get_doctors():
    conn = connect()
    rows = conn.execute("SELECT * FROM doctor ORDER BY full_name").fetchall()
    conn.close()
    return rows


def get_free_slots(doctor_id, day_text):
    try:
        day = datetime.strptime(day_text, "%Y-%m-%d")
    except ValueError:
        return []
    conn = connect()
    rule = conn.execute(
        "SELECT * FROM doctor_availability WHERE doctor_id = ? AND day_of_week = ?",
        (doctor_id, day.isoweekday()),
    ).fetchone()
    if rule is None:
        conn.close()
        return []
    taken = [
        r["appt_time"]
        for r in conn.execute(
            "SELECT appt_time FROM appointment WHERE doctor_id = ? AND appt_date = ? AND status = 'Booked'",
            (doctor_id, day_text),
        )
    ]
    conn.close()
    slots = []
    current = datetime.strptime(rule["start_time"], "%H:%M")
    end = datetime.strptime(rule["end_time"], "%H:%M")
    step = timedelta(minutes=rule["slot_minutes"])
    while current + step <= end:
        text = current.strftime("%H:%M")
        if text not in taken:
            slots.append(text)
        current += step
    return slots


def book_appointment(patient_id, doctor_id, day_text, time_text, reason):
    if patient_id is None:
        return False, "Select a patient first (Patients tab)."
    try:
        day = datetime.strptime(day_text, "%Y-%m-%d").date()
    except ValueError:
        return False, "Date must look like 2026-12-01."
    if day < date.today():
        return False, "Choose today or a future date."
    if time_text not in get_free_slots(doctor_id, day_text):
        return False, "That slot is not available. Choose another."
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO appointment (patient_id, doctor_id, appt_date, appt_time, reason) "
            "VALUES (?,?,?,?,?)",
            (patient_id, doctor_id, day_text, time_text, reason),
        )
        conn.commit()
        return True, "Appointment booked."
    except sqlite3.IntegrityError:
        return False, "That slot was just taken. Choose another."
    finally:
        conn.close()


def list_appointments(day_text):
    conn = connect()
    rows = conn.execute(
        "SELECT a.appointment_id, a.appt_time, p.first_name || ' ' || p.last_name AS patient, "
        "d.full_name AS doctor, a.reason, a.status FROM appointment a "
        "JOIN patient p ON p.patient_id = a.patient_id JOIN doctor d ON d.doctor_id = a.doctor_id "
        "WHERE a.appt_date = ? ORDER BY a.appt_time",
        (day_text,),
    ).fetchall()
    conn.close()
    return rows


def cancel_appointment(appointment_id):
    conn = connect()
    cur = conn.execute(
        "UPDATE appointment SET status = 'Cancelled' "
        "WHERE appointment_id = ? AND status = 'Booked'",
        (appointment_id,),
    )
    conn.commit()
    conn.close()
    return (
        (True, "Appointment cancelled.")
        if cur.rowcount
        else (False, "Only booked appointments can be cancelled.")
    )


def get_booked_for_patient(patient_id):
    conn = connect()
    rows = conn.execute(
        "SELECT a.appointment_id, a.appt_date, a.appt_time, d.full_name FROM appointment a "
        "JOIN doctor d ON d.doctor_id = a.doctor_id "
        "WHERE a.patient_id = ? AND a.status = 'Booked' ORDER BY a.appt_date, a.appt_time",
        (patient_id,),
    ).fetchall()
    conn.close()
    return rows


def record_visit(appointment_id, complaint, treatment, notes, follow_up):
    if appointment_id is None:
        return False, "Choose an appointment first."
    if not complaint:
        return False, "Please enter the complaint."
    if follow_up:
        try:
            datetime.strptime(follow_up, "%Y-%m-%d")
        except ValueError:
            return False, "Follow-up date must look like 2026-12-01."
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO visit (appointment_id, complaint, treatment, notes, follow_up_date) "
            "VALUES (?,?,?,?,?)",
            (appointment_id, complaint, treatment, notes, follow_up),
        )
        conn.execute(
            "UPDATE appointment SET status = 'Completed' WHERE appointment_id = ?",
            (appointment_id,),
        )
        conn.commit()
        return True, "Visit saved."
    except sqlite3.IntegrityError:
        return False, "A visit is already recorded for this appointment."
    finally:
        conn.close()


def get_history(patient_id):
    conn = connect()
    rows = conn.execute(
        "SELECT a.appt_date, d.full_name AS doctor, v.complaint, v.treatment, v.follow_up_date "
        "FROM visit v JOIN appointment a ON a.appointment_id = v.appointment_id "
        "JOIN doctor d ON d.doctor_id = a.doctor_id WHERE a.patient_id = ? "
        "ORDER BY a.appt_date DESC",
        (patient_id,),
    ).fetchall()
    conn.close()
    return rows


def export_csv(rows, filename):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time", "Patient", "Doctor", "Reason", "Status"])
        for r in rows:
            writer.writerow(
                [r["appt_time"], r["patient"], r["doctor"], r["reason"], r["status"]]
            )


def backup_database(folder):
    name = "clinic-backup-" + date.today().isoformat() + ".db"
    try:
        shutil.copy2(DB_FILE, os.path.join(folder, name))
        return True, "Backup saved as " + name
    except OSError:
        return False, "Backup failed. Check the drive or folder."


def fill_table(tree, rows):
    tree.delete(*tree.get_children())
    for row in rows:
        tree.insert("", "end", values=row)


def make_table(parent, columns, height=8):
    tree = ttk.Treeview(parent, columns=columns, show="headings", height=height)
    for col in columns:
        tree.heading(col, text=col)
        tree.column(col, width=110)
    return tree


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sunrise Community Clinic")
        self.geometry("900x560")
        self.user = None
        self.patient_id = None
        self.show_login()

    def show_login(self):
        for w in self.winfo_children():
            w.destroy()
        frame = ttk.Frame(self, padding=40)
        frame.pack(expand=True)
        ttk.Label(frame, text="Staff Login", font=("Arial", 18, "bold")).grid(
            row=0, column=0, columnspan=2, pady=10
        )
        ttk.Label(frame, text="Username").grid(row=1, column=0, sticky="e", pady=5)
        ttk.Label(frame, text="Password").grid(row=2, column=0, sticky="e", pady=5)
        self.user_var, self.pass_var = tk.StringVar(), tk.StringVar()
        ttk.Entry(frame, textvariable=self.user_var).grid(row=1, column=1, pady=5)
        ttk.Entry(frame, textvariable=self.pass_var, show="*").grid(row=2, column=1, pady=5)
        ttk.Button(frame, text="Log in", command=self.do_login).grid(
            row=3, column=0, columnspan=2, pady=15
        )
        self.bind("<Return>", lambda event: self.do_login())

    def do_login(self):
        user, message = login(self.user_var.get().strip(), self.pass_var.get())
        if user is None:
            messagebox.showerror("Login", message)
            return
        self.user = user
        self.unbind("<Return>")
        self.show_main()

    def show_main(self):
        for w in self.winfo_children():
            w.destroy()
        top = ttk.Frame(self, padding=5)
        top.pack(fill="x")
        ttk.Label(
            top, text="Logged in as " + self.user["username"] + " (" + self.user["role"] + ")"
        ).pack(side="left")
        ttk.Button(top, text="Log out", command=self.logout).pack(side="right")
        ttk.Button(top, text="Backup", command=self.do_backup).pack(side="right", padx=5)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=5, pady=5)
        self.build_patients_tab()
        self.build_appointments_tab()
        self.build_visits_tab()
        self.build_reports_tab()

    def logout(self):
        self.user, self.patient_id = None, None
        self.show_login()

    def do_backup(self):
        if not can_do(self.user["role"], "backup"):
            messagebox.showwarning("Backup", "Only the administrator can make a backup.")
            return
        folder = filedialog.askdirectory(title="Choose a folder or drive for the backup")
        if folder:
            ok, message = backup_database(folder)
            (messagebox.showinfo if ok else messagebox.showerror)("Backup", message)

    def build_patients_tab(self):
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Patients")
        form = ttk.LabelFrame(tab, text="Register new patient", padding=8)
        form.pack(fill="x")
        self.reg = {}
        fields = ["ID number", "First name", "Last name", "Birth date", "Phone", "Address"]
        for i, name in enumerate(fields):
            ttk.Label(form, text=name).grid(row=i // 3 * 2, column=i % 3, sticky="w", padx=5)
            self.reg[name] = tk.StringVar()
            ttk.Entry(form, textvariable=self.reg[name], width=25).grid(
                row=i // 3 * 2 + 1, column=i % 3, padx=5, pady=2
            )
        ttk.Button(form, text="Save patient", command=self.do_register).grid(
            row=4, column=0, pady=6, sticky="w", padx=5
        )
        search = ttk.Frame(tab)
        search.pack(fill="x", pady=8)
        self.search_var = tk.StringVar()
        ttk.Label(search, text="Search (name, ID or phone):").pack(side="left")
        ttk.Entry(search, textvariable=self.search_var, width=30).pack(side="left", padx=5)
        ttk.Button(search, text="Search", command=self.do_search).pack(side="left")
        self.patient_tree = make_table(
            tab, ("ID", "ID number", "First name", "Last name", "Phone")
        )
        self.patient_tree.pack(fill="both", expand=True)
        self.patient_tree.bind("<<TreeviewSelect>>", self.pick_patient)
        self.selected_label = ttk.Label(
            tab, text="Selected patient: none", font=("Arial", 10, "bold")
        )
        self.selected_label.pack(anchor="w", pady=4)
        self.do_search()

    def do_register(self):
        if not can_do(self.user["role"], "register_patient"):
            messagebox.showwarning("Register", "You are not allowed to register patients.")
            return
        v = {k: var.get().strip() for k, var in self.reg.items()}
        ok, message = register_patient(
            v["ID number"],
            v["First name"],
            v["Last name"],
            v["Birth date"],
            v["Phone"],
            v["Address"],
        )
        (messagebox.showinfo if ok else messagebox.showerror)("Register", message)
        if ok:
            for var in self.reg.values():
                var.set("")
            self.do_search()

    def do_search(self):
        rows = find_patients(self.search_var.get().strip())
        fill_table(
            self.patient_tree,
            [
                (r["patient_id"], r["national_id"], r["first_name"], r["last_name"], r["phone"])
                for r in rows
            ],
        )

    def pick_patient(self, event):
        chosen = self.patient_tree.selection()
        if not chosen:
            return
        values = self.patient_tree.item(chosen[0])["values"]
        self.patient_id = int(values[0])
        self.selected_label.config(
            text="Selected patient: " + str(values[2]) + " " + str(values[3])
        )
        self.appt_patient_label.config(text="Patient: " + str(values[2]) + " " + str(values[3]))
        self.visit_patient_label.config(
            text="Patient: " + str(values[2]) + " " + str(values[3])
        )
        self.refresh_visit_tab()

    def build_appointments_tab(self):
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Appointments")
        self.appt_patient_label = ttk.Label(
            tab,
            text="Patient: none selected (use the Patients tab)",
            font=("Arial", 10, "bold"),
        )
        self.appt_patient_label.pack(anchor="w")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=6)
        self.doctors = get_doctors()
        ttk.Label(row, text="Doctor").pack(side="left")
        self.doctor_var = tk.StringVar()
        ttk.Combobox(
            row,
            textvariable=self.doctor_var,
            state="readonly",
            width=20,
            values=[d["full_name"] for d in self.doctors],
        ).pack(side="left", padx=5)
        ttk.Label(row, text="Date (YYYY-MM-DD)").pack(side="left")
        self.date_var = tk.StringVar(value=(date.today() + timedelta(days=1)).isoformat())
        ttk.Entry(row, textvariable=self.date_var, width=12).pack(side="left", padx=5)
        ttk.Button(row, text="Show free slots", command=self.show_slots).pack(side="left")
        row2 = ttk.Frame(tab)
        row2.pack(fill="x", pady=6)
        self.slot_list = tk.Listbox(row2, height=7, width=12, exportselection=False)
        self.slot_list.pack(side="left")
        side = ttk.Frame(row2)
        side.pack(side="left", padx=15)
        ttk.Label(side, text="Reason for visit").pack(anchor="w")
        self.reason_var = tk.StringVar()
        ttk.Entry(side, textvariable=self.reason_var, width=40).pack(anchor="w", pady=3)
        ttk.Button(side, text="Book selected slot", command=self.do_book).pack(
            anchor="w", pady=3
        )
        ttk.Label(tab, text="Appointments on the chosen date").pack(anchor="w", pady=(10, 0))
        self.day_tree = make_table(
            tab, ("ID", "Time", "Patient", "Doctor", "Reason", "Status"), height=6
        )
        self.day_tree.pack(fill="both", expand=True)
        ttk.Button(tab, text="Cancel selected appointment", command=self.do_cancel).pack(
            anchor="w", pady=4
        )

    def current_doctor_id(self):
        for d in self.doctors:
            if d["full_name"] == self.doctor_var.get():
                return d["doctor_id"]
        return None

    def show_slots(self):
        doctor_id = self.current_doctor_id()
        if doctor_id is None:
            messagebox.showerror("Slots", "Choose a doctor first.")
            return
        self.slot_list.delete(0, "end")
        slots = get_free_slots(doctor_id, self.date_var.get().strip())
        for s in slots:
            self.slot_list.insert("end", s)
        if not slots:
            messagebox.showinfo(
                "Slots", "No free slots (doctor does not work that day, or the date is wrong)."
            )
        self.refresh_day()

    def refresh_day(self):
        rows = list_appointments(self.date_var.get().strip())
        fill_table(self.day_tree, [tuple(r) for r in rows])

    def do_book(self):
        if not can_do(self.user["role"], "book"):
            messagebox.showwarning("Book", "You are not allowed to book appointments.")
            return
        picked = self.slot_list.curselection()
        if not picked:
            messagebox.showerror("Book", "Click a free slot first.")
            return
        ok, message = book_appointment(
            self.patient_id,
            self.current_doctor_id(),
            self.date_var.get().strip(),
            self.slot_list.get(picked[0]),
            self.reason_var.get().strip(),
        )
        (messagebox.showinfo if ok else messagebox.showerror)("Book", message)
        if ok:
            self.reason_var.set("")
            self.show_slots()
            self.refresh_visit_tab()

    def do_cancel(self):
        chosen = self.day_tree.selection()
        if not chosen:
            messagebox.showerror("Cancel", "Click an appointment first.")
            return
        ok, message = cancel_appointment(int(self.day_tree.item(chosen[0])["values"][0]))
        (messagebox.showinfo if ok else messagebox.showerror)("Cancel", message)
        self.show_slots()

    def build_visits_tab(self):
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Visits")
        self.visit_patient_label = ttk.Label(
            tab,
            text="Patient: none selected (use the Patients tab)",
            font=("Arial", 10, "bold"),
        )
        self.visit_patient_label.pack(anchor="w")
        ttk.Label(tab, text="Visit history").pack(anchor="w", pady=(8, 0))
        self.history_tree = make_table(
            tab, ("Date", "Doctor", "Complaint", "Treatment", "Follow-up"), height=6
        )
        self.history_tree.pack(fill="x")
        form = ttk.LabelFrame(tab, text="Record new visit (doctor or nurse)", padding=8)
        form.pack(fill="x", pady=10)
        ttk.Label(form, text="Booked appointment").grid(row=0, column=0, sticky="w")
        self.appt_choice = tk.StringVar()
        self.appt_box = ttk.Combobox(
            form, textvariable=self.appt_choice, state="readonly", width=45
        )
        self.appt_box.grid(row=1, column=0, columnspan=2, sticky="w", padx=3, pady=2)
        self.visit_vars = {}
        for i, name in enumerate(["Complaint", "Treatment", "Notes", "Follow-up date"]):
            ttk.Label(form, text=name).grid(
                row=2 + i // 2 * 2, column=i % 2, sticky="w", padx=3
            )
            self.visit_vars[name] = tk.StringVar()
            ttk.Entry(form, textvariable=self.visit_vars[name], width=35).grid(
                row=3 + i // 2 * 2, column=i % 2, padx=3, pady=2
            )
        ttk.Button(form, text="Save visit", command=self.do_visit).grid(
            row=7, column=0, sticky="w", pady=6, padx=3
        )
        self.booked = []

    def refresh_visit_tab(self):
        if self.patient_id is None:
            return
        fill_table(self.history_tree, [tuple(r) for r in get_history(self.patient_id)])
        self.booked = get_booked_for_patient(self.patient_id)
        self.appt_box["values"] = [
            str(r["appt_date"]) + " " + r["appt_time"] + " with " + r["full_name"]
            for r in self.booked
        ]
        self.appt_choice.set("")

    def do_visit(self):
        if not can_do(self.user["role"], "record_visit"):
            messagebox.showwarning("Visit", "Only doctors and nurses can record visits.")
            return
        index = self.appt_box.current()
        appointment_id = self.booked[index]["appointment_id"] if index >= 0 else None
        v = {k: var.get().strip() for k, var in self.visit_vars.items()}
        ok, message = record_visit(
            appointment_id, v["Complaint"], v["Treatment"], v["Notes"], v["Follow-up date"]
        )
        (messagebox.showinfo if ok else messagebox.showerror)("Visit", message)
        if ok:
            for var in self.visit_vars.values():
                var.set("")
            self.refresh_visit_tab()

    def build_reports_tab(self):
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Daily report")
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Label(row, text="Date (YYYY-MM-DD)").pack(side="left")
        self.report_date = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(row, textvariable=self.report_date, width=12).pack(side="left", padx=5)
        ttk.Button(row, text="Show report", command=self.show_report).pack(side="left")
        ttk.Button(row, text="Export CSV", command=self.do_export).pack(side="left", padx=5)
        self.report_tree = make_table(
            tab, ("ID", "Time", "Patient", "Doctor", "Reason", "Status"), height=14
        )
        self.report_tree.pack(fill="both", expand=True, pady=8)
        self.total_label = ttk.Label(tab, text="Total appointments: 0")
        self.total_label.pack(anchor="w")

    def show_report(self):
        self.report_rows = list_appointments(self.report_date.get().strip())
        fill_table(self.report_tree, [tuple(r) for r in self.report_rows])
        self.total_label.config(text="Total appointments: " + str(len(self.report_rows)))

    def do_export(self):
        self.show_report()
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="report-" + self.report_date.get() + ".csv"
        )
        if filename:
            export_csv(self.report_rows, filename)
            messagebox.showinfo("Export", "Report saved.")


if __name__ == "__main__":
    create_tables()
    add_sample_data()
    App().mainloop()