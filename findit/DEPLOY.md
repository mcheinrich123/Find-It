# Putting FindIt online

FindIt runs on **PythonAnywhere**, a host made for Python and Flask. The free plan gives the site an address like `https://YOURNAME.pythonanywhere.com`. It keeps the database (`instance/database.db`) and uploaded photos (`static/uploads/`) on a real disk, so they survive restarts.

> **Why not Netlify, Vercel, GitHub Pages or Render's free plan?** The first three only host static files or short-lived functions, so the Flask backend can't run on them. Render's free plan wipes its disk on every restart, which would delete every account, report and photo.

Throughout this guide, replace `YOURNAME` with your PythonAnywhere username.

> **Reading the commands:** open this file in preview (**Ctrl+Shift+V** in VS Code) to see each command in a gray box. In the raw file, the commands sit between lines of three backticks (```` ``` ````). Don't copy those lines: in the Bash console a backtick makes it wait for more input. If that happens, press **Ctrl+C**. Type each command and press **Enter** before you type the next.

---

## 1. Create the account

1. Go to <https://www.pythonanywhere.com> and choose **Pricing & signup → Create a Beginner account** (free).
2. Choose your username carefully, because it becomes the site's address. For example, `finditdlsud` gives `finditdlsud.pythonanywhere.com`.

## 2. Upload the code

1. Open the **Files** tab and click **Upload a file**. Upload `findit-upload.zip` (it's next to the `findit` folder).
2. Open the **Consoles** tab and start a **Bash** console. Run these two commands:

   ```sh
   unzip findit-upload.zip
   ls findit
   ```

   The second one should list `app.py  create_admin.py  nlp.py  requirements.txt  static  templates`. If `unzip` says it can't find the file, the zip isn't in your home folder: upload it again on the **Files** tab into `/home/YOURNAME/`.

## 3. Install Flask

In the same Bash console:

```sh
mkvirtualenv --python=python3.13 findit-venv
pip install -r ~/findit/requirements.txt
```

If `python3.13` isn't found, use the newest version that the **Web** tab offers. Any version from 3.10 up works. The prompt now starts with `(findit-venv)`.

## 4. Create the web app

1. Open the **Web** tab, click **Add a new web app**, then click **Next**.
2. Choose **Manual configuration** (not "Flask"), then choose the **same Python version** as in step 3.
3. On the page that opens, fill in:

   | Setting | Value |
   |---|---|
   | Source code | `/home/YOURNAME/findit` |
   | Working directory | `/home/YOURNAME/findit` |
   | Virtualenv | `/home/YOURNAME/.virtualenvs/findit-venv` |

4. Under **Static files**, add one row:

   | URL | Directory |
   |---|---|
   | `/static/` | `/home/YOURNAME/findit/static/` |

5. Under **Security**, turn **Force HTTPS** on. Logins won't work over plain `http://`, because the login cookie is HTTPS-only.

## 5. Set the secret key

The secret key signs the login cookie, and anyone who knows it can log in as anyone. Create one in the Bash console:

```sh
python -c "import secrets; print(secrets.token_hex(32))"
```

On the **Web** tab, click the **WSGI configuration file** link (`/var/www/YOURNAME_pythonanywhere_com_wsgi.py`), **delete everything in it**, paste the following and click **Save**:

```python
import os
import sys

path = '/home/YOURNAME/findit'
if path not in sys.path:
    sys.path.insert(0, path)

os.environ['SECRET_KEY'] = 'PASTE-THE-64-CHARACTER-KEY-HERE'
os.environ['SESSION_COOKIE_SECURE'] = '1'

from app import app as application
```

Never commit this key, paste it in a chat, or reuse the example above.

## 6. Create the SWAFO admin account

The live site starts with an empty database. Create the first admin from the Bash console:

```sh
cd ~/findit
workon findit-venv
export ADMIN_NAME="SWAFO Admin" ADMIN_SCHOOL_ID="SWAFO-ADMIN" ADMIN_EMAIL="swafo@dlsud.edu.ph"
read -s -p "Admin password (8+ characters): " ADMIN_PASSWORD; echo; export ADMIN_PASSWORD
python create_admin.py
```

Change the name, ID and email to the real ones. Because `read -s` doesn't show the password or save it in the console history, it isn't left lying around. You'll see `WARNING: SECRET_KEY is not set` here. You can ignore it: the console doesn't need the key, only the website does.

Students create their own accounts on the **Sign up** page.

## 7. Go live

On the **Web** tab, click the green **Reload** button, then open `https://YOURNAME.pythonanywhere.com`.

If you get "Something went wrong", check **Web tab → Log files → Error log**. The last lines say what failed. Most often it's a typo in a path in step 4 or the WSGI file.

---

## Keeping it running

- **Every 3 months:** on the free plan, open the **Web** tab and click **Run until 3 months from today**, or the site switches off. PythonAnywhere emails a reminder first.
- **Back up the data** now and then. In a Bash console, run:

  ```sh
  cd ~ && zip -r findit-backup-$(date +%F).zip findit/instance findit/static/uploads
  ```

  Then download that zip from the **Files** tab.

## Updating the site

1. On the **Files** tab, go to the file, for example `findit/static/js/items.js`, and upload the new version over it. If many files changed, upload a new zip and `unzip -o` it.
2. Click **Reload** on the **Web** tab. Template and Python changes need a reload to show up. For CSS and JS, refresh the browser too.

**Never overwrite `instance/` or `static/uploads/`.** They hold the live database and the photos people uploaded. When you build a new zip of the `findit` folder, leave these out: `instance/`, `static/uploads/`, `__pycache__/`, `old/` and `findit.db`.

If `requirements.txt` changed, run `workon findit-venv && pip install -r ~/findit/requirements.txt` before you reload.

## Limits of the free plan

- The address is `YOURNAME.pythonanywhere.com`. A custom domain such as `findit.dlsud.edu.ph` needs a paid plan.
- Disk space is small, and uploaded photos count toward it. A paid plan raises this and the daily CPU allowance.
- It handles a school pilot comfortably. If the site outgrows SQLite, move the database to MySQL (included on PythonAnywhere) or PostgreSQL by changing `SQLALCHEMY_DATABASE_URI` in `app.py`.

## Moving to another host later

Any host with a **persistent disk** works, such as Railway with a volume, Render with a paid disk, or a VPS. Add `gunicorn` to `requirements.txt` and start the app with `gunicorn app:app` from the `findit` folder. Set `SECRET_KEY` and `SESSION_COOKIE_SECURE=1` as environment variables, and keep `instance/` and `static/uploads/` on the persistent disk.
