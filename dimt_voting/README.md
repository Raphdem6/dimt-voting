# DIMT Student Voting System (Django)

Students log in with their **full name + ID number** (no passwords). Only students the admin has uploaded can vote.
A public live-results dashboard with graphs is open to everyone.

## Run it
```bash
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser                        # your admin account (use a LONG password)
python manage.py runserver
```
Optional demo data: `python manage.py seed_demo` (prints a random admin password and a sample student login).

| Page | Address | Login |
|---|---|---|
| Elections | `/` | none |
| Candidates and their programmes | `/candidates/` | none |
| Live results + graphs | `/results/` | none |
| Am I on the list? | `/check/` | none |
| Student login | `/login/` | name + ID |
| Login problem ("error handler") | `/login-help/` | none |
| **Admin panel (secret address)** | **`/dimtmwelection/`** | staff |

Change the secret address with the environment variable `DJANGO_ADMIN_URL` (must end with `/`).

## Admin tasks (all inside `/dimtmwelection/`)
1. **Upload the student list**: Students > "Upload student list (CSV)". Columns: `student_id, full_name, department, phone`
   (see `sample_students.csv`; you can use `first_name, last_name` instead of `full_name`). Re-upload any time; existing
   students are updated. Tick the option to mark anyone missing from the file as not eligible.
   The same can be done with `python manage.py import_students file.csv`.
2. **Create the election**: Elections > Add, with its Positions. Then add **Candidates** (photo, short statement) and their
   **Programmes** (as many as you like, shown on the ballot and the public Candidates page).
3. **Website background**: "Website appearance" > upload a background image, pick a colour and how much to fade the image.
4. **Login problems**: "Login issues" shows every ID students sent through the error-handler page, whether that ID is on
   the list, and the registered name, so you can fix a spelling or add the student. Mark them resolved.
5. **Security**: "Blocked devices" lists everyone caught probing `/admin`; delete a row to unblock. Students can be
   un-blacklisted from the Students list (action menu). Command line: `python manage.py unblock_ip 1.2.3.4 --student DIMT001`.
6. Public results: choose *Live*, *Turnout only* (recommended) or *Hidden* per election. Kill switch and Certify are in the Elections action menu.

## How students log in
Name and ID must both match the uploaded list. Capital letters, extra spaces and word order don't matter
("banda chikondi" = "Chikondi Banda"). If login fails, the page offers **Send my ID to the error handler**. The `/check/`
page tells a student whether their ID is on the list (name shown partly hidden, e.g. `C******* B****`).

## Security built in
- Secret admin address; **`/admin`, `/wp-admin`, `/phpmyadmin` ... are traps**: the visitor's browser (and IP) is blocked
  from logging in and voting, and a logged-in student is blacklisted. Staff are never trapped.
- Admin login is limited to 5 failed tries per IP per 15 minutes. Use a long, unique admin password.
- Student login: 5 wrong tries lock that ID for 15 minutes; 12 failures from one device block that device for 15 minutes.
  Same error message whether the ID or name was wrong.
- Only uploaded, eligible, non-blacklisted students can vote. One vote per student, enforced by the database.
- Ballot secrecy: who voted and what they chose are stored separately and never linked; vote times are rounded to the hour.
- Strict Content-Security-Policy (no inline scripts, no outside scripts: Chart.js is bundled), clickjacking/sniffing protection,
  CSRF protection, session rotation at login, 30-minute idle logout, no-store caching on login/vote pages.
- Votes, voter records and the hash-chained audit log can't be edited in the admin. `/dimtmwelection/`-only check: `/audit/verify/` (when logged in as staff).
- Uploads: images are verified (Pillow), max 5 MB; roll files must be .csv, max 3 MB.

## Before real use
1. Serve over **HTTPS** and set: `DJANGO_HTTPS=1`, `DJANGO_ALLOWED_HOSTS=your.domain`, `DJANGO_CSRF_ORIGINS=https://your.domain`.
2. Behind nginx/Apache set `BEHIND_PROXY=1` and make the proxy pass `X-Forwarded-For` ($remote_addr) and `X-Forwarded-Proto`.
3. **Shared campus Wi-Fi:** if many students share one public IP, set `DJANGO_BLOCK_BY_IP=0` so one person probing `/admin`
   cannot block everyone. The prober's own browser and (if logged in) their student account are still blocked.
4. Debug is OFF by default (`DJANGO_DEBUG=1` only on your own computer). A random secret key is created in `.secret_key`
   on first run; keep that file private. Back up `db.sqlite3` (or use PostgreSQL; example in `config/settings.py`).
5. Run with gunicorn behind nginx; let nginx serve `/media/` for speed.

No website can be promised 100% unhackable. This setup closes the common doors; also keep the server updated, keep the admin
password private, and consider adding two-factor login for admins (e.g. `django-otp`).

Tests: `python manage.py test voting`
