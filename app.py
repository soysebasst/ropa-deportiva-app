from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, send_from_directory, session, Response
import csv, io
from werkzeug.utils import secure_filename
from pathlib import Path
from datetime import datetime
import sqlite3, uuid, os, shutil, hmac

BASE = Path(__file__).resolve().parent

# En local, DATA_DIR no se configura y todo queda donde siempre (junto al
# código). En producción (Render, Railway, etc.) se define la variable de
# entorno DATA_DIR apuntando al disco persistente, y la base de datos y las
# imágenes se guardan ahí en vez de en el propio código de la app.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "inventario.db"
UPLOADS = DATA_DIR / "uploads"
UPLOADS.mkdir(exist_ok=True)

# Carpeta con una copia de los datos reales, incluida una sola vez en el
# repositorio, para poblar el disco persistente la primera vez que la app
# arranca en un servidor nuevo. Una vez el disco tiene su propio
# inventario.db, esta copia de siembra nunca se vuelve a tocar.
SEED_DIR = BASE / "seed_data"


def seed_data_if_needed():
    if DATA_DIR.resolve() == BASE.resolve():
        return  # desarrollo local: nada que sembrar
    if DB.exists():
        return  # el disco ya tiene datos reales, no los pisamos
    seed_db = SEED_DIR / "inventario.db"
    if seed_db.exists():
        shutil.copy2(seed_db, DB)
    seed_uploads = SEED_DIR / "uploads"
    if seed_uploads.exists():
        for item in seed_uploads.iterdir():
            dest = UPLOADS / item.name
            if not dest.exists():
                shutil.copy2(item, dest)


seed_data_if_needed()

app = Flask(__name__)

_default_secret = "cambia-esta-clave"
app.secret_key = os.environ.get("SECRET_KEY", _default_secret)
if app.secret_key == _default_secret:
    print("ADVERTENCIA: usando SECRET_KEY de ejemplo. Antes de publicar en internet, define la variable de entorno SECRET_KEY con un valor propio y secreto.")

app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

# ---------------------------------------------------------------------------
# Acceso al administrador (usuario y contraseña por variables de entorno)
# ---------------------------------------------------------------------------
_default_admin_user = "admin"
_default_admin_pass = "admin"
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", _default_admin_user)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", _default_admin_pass)
if ADMIN_PASSWORD == _default_admin_pass:
    print("ADVERTENCIA: usando ADMIN_USERNAME/ADMIN_PASSWORD de ejemplo (admin/admin). Antes de publicar en internet, define tus propias credenciales por variable de entorno.")

# Rutas que cualquiera puede ver sin iniciar sesión: el catálogo público,
# su API de solo lectura, las imágenes subidas, los archivos estáticos y la
# propia pantalla de login. Todo lo demás exige haber iniciado sesión.
PUBLIC_PATH_PREFIXES = ("/catalogo", "/api/catalogo", "/uploads/", "/static/", "/login")


@app.before_request
def require_admin_login():
    path = request.path
    if path.startswith(PUBLIC_PATH_PREFIXES):
        return
    if session.get("admin_logged_in"):
        return
    return redirect(url_for("login", next=path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        ok_user = hmac.compare_digest(user, ADMIN_USERNAME)
        ok_pass = hmac.compare_digest(pw, ADMIN_PASSWORD)
        if ok_user and ok_pass:
            session.clear()
            session["admin_logged_in"] = True
            session.permanent = True
            dest = request.form.get("next") or url_for("index")
            return redirect(dest)
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

ALLOWED = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_LOGO = ALLOWED | {"svg"}

DEFAULT_CATEGORIES = ["Tops", "Shorts", "Camisas", "Conjuntos", "Licras"]
DEFAULT_SETTINGS = {
    "brand_name": "sportswear_ST",
    "logo": "",
    "primary_color": "#97a2ff",
    "accent_color": "#2d42ff",
    "tagline": "Ropa deportiva para tu estilo.",
    "short_description": "Catálogo oficial de sportswear_ST: ropa deportiva moderna para tu día a día.",
    "instagram_url": "",
    "whatsapp_number": "",
}


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _column_names(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table, col, decl):
    if col not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db():
    """Crea las tablas si no existen y migra las existentes de forma aditiva.
    Nunca borra tablas ni filas existentes."""
    conn = db()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            cost REAL NOT NULL DEFAULT 0,
            sale_price REAL NOT NULL DEFAULT 0,
            sizes TEXT DEFAULT '',
            image TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS colors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            color TEXT NOT NULL,
            image TEXT,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            sale_price REAL NOT NULL DEFAULT 0,
            image TEXT
        );
        CREATE TABLE IF NOT EXISTS set_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(set_id) REFERENCES sets(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS set_colors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_id INTEGER NOT NULL,
            color TEXT NOT NULL,
            image TEXT,
            FOREIGN KEY(set_id) REFERENCES sets(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_date TEXT NOT NULL,
            product_ref TEXT DEFAULT '',
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            cost REAL NOT NULL DEFAULT 0,
            sale_price REAL NOT NULL DEFAULT 0,
            shipping_cost REAL NOT NULL DEFAULT 0,
            shipping_payer TEXT NOT NULL DEFAULT 'ninguno',
            customer_name TEXT DEFAULT '',
            customer_phone TEXT DEFAULT '',
            customer_location TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # Columnas nuevas en tablas ya existentes (no se pierde nada)
    _add_column(conn, "products", "description", "TEXT DEFAULT ''")
    _add_column(conn, "products", "updated_at", "TEXT")
    _add_column(conn, "products", "published", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "sets", "description", "TEXT DEFAULT ''")
    _add_column(conn, "sets", "updated_at", "TEXT")
    _add_column(conn, "sets", "published", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "sets", "sizes", "TEXT DEFAULT ''")

    conn.execute("UPDATE products SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) WHERE updated_at IS NULL")
    conn.execute("UPDATE sets SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
    conn.execute("UPDATE products SET published = 1 WHERE published IS NULL")
    conn.execute("UPDATE sets SET published = 1 WHERE published IS NULL")

    for i, cat in enumerate(DEFAULT_CATEGORIES):
        conn.execute("INSERT OR IGNORE INTO categories(name, sort_order) VALUES (?, ?)", (cat, i))

    existing_cats = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND TRIM(category) <> ''"
        )
    ]
    for cat in existing_cats:
        conn.execute("INSERT OR IGNORE INTO categories(name, sort_order) VALUES (?, 999)", (cat,))

    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()


# Se ejecuta siempre al importar la app (tanto con "python app.py" en local
# como con gunicorn en producción), para que las tablas y migraciones
# existan sin depender de cómo se arranque el servidor.
init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_settings(conn):
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in rows})
    return s


def get_categories(conn, exclude_conjuntos=False):
    q = "SELECT id, name, sort_order FROM categories"
    if exclude_conjuntos:
        q += " WHERE name <> 'Conjuntos'"
    q += " ORDER BY sort_order, name"
    return [dict(r) for r in conn.execute(q).fetchall()]


def distinct_colors(conn):
    rows = conn.execute("SELECT DISTINCT color FROM colors WHERE color IS NOT NULL AND TRIM(color) <> '' ORDER BY color").fetchall()
    return [r["color"] for r in rows]


def distinct_sizes(conn):
    rows = conn.execute("SELECT sizes FROM products WHERE sizes IS NOT NULL AND TRIM(sizes) <> ''").fetchall()
    out = set()
    for r in rows:
        for token in (r["sizes"] or "").split(","):
            token = token.strip()
            if token:
                out.add(token)
    return sorted(out)


def digits_only(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


COLOR_HEX_MAP = {
    "negro": "#111111", "black": "#111111", "negro mate": "#1a1a1a",
    "blanco": "#ffffff", "white": "#ffffff", "hueso": "#f2ede1", "marfil": "#f2ede1",
    "rosado": "#f472b6", "rosa": "#f472b6", "pink": "#f472b6", "fucsia": "#e0249a",
    "azul": "#2d42ff", "blue": "#2d42ff", "celeste": "#7dd3fc", "azul cielo": "#7dd3fc",
    "azul rey": "#1e3a8a", "navy": "#1e3a8a", "azul marino": "#1e3a8a", "azul petroleo": "#0f4c5c", "azul petróleo": "#0f4c5c",
    "rojo": "#ef4444", "red": "#ef4444", "coral": "#ff6f61", "salmon": "#fa8072", "salmón": "#fa8072",
    "verde": "#22c55e", "green": "#22c55e", "verde militar": "#4b5320", "verde oliva": "#6b7a3a", "oliva": "#6b7a3a",
    "verde menta": "#7fd8be", "menta": "#7fd8be", "verde limon": "#a3e635", "verde limón": "#a3e635",
    "gris": "#9ca3af", "grey": "#9ca3af", "gray": "#9ca3af", "gris claro": "#d1d5db", "gris oscuro": "#4b5563",
    "gris jaspe": "#a8a8a8", "gris jaspeado": "#a8a8a8", "plomo": "#6b7280",
    "amarillo": "#eab308", "yellow": "#eab308", "mostaza": "#c9971b",
    "naranja": "#f97316", "orange": "#f97316",
    "morado": "#a855f7", "purple": "#a855f7", "violeta": "#a855f7", "lila": "#c4b5fd", "lavanda": "#b9a5e8",
    "beige": "#e7d9c4", "crema": "#f5ecd9", "arena": "#d9c6a5", "kaki": "#c3b091", "caqui": "#c3b091",
    "café": "#7c4a2d", "cafe": "#7c4a2d", "marron": "#7c4a2d", "marrón": "#7c4a2d", "vinotinto": "#7f1d3a", "vino": "#7f1d3a",
    "turquesa": "#14b8a6", "aqua": "#22d3d8", "dorado": "#d4af37", "plateado": "#c0c0c0", "perla": "#e7e5e0",
}


def color_hex(name):
    return COLOR_HEX_MAP.get((name or "").strip().lower(), "#97a2ff")


def save_image(file, allowed=ALLOWED):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        return None
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(UPLOADS / name)
    return name


def normalize_sizes(raw):
    return ", ".join(sorted({s.strip() for s in (raw or "").split(",") if s.strip()}, key=lambda x: x))


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@app.template_filter("money")
def money(v):
    try:
        return "${:,.0f}".format(float(v or 0))
    except (ValueError, TypeError):
        return "$0"


@app.template_filter("wanumber")
def wanumber(v):
    return digits_only(v)


@app.template_filter("colorhex")
def colorhex_filter(v):
    return color_hex(v)


@app.context_processor
def inject_globals():
    conn = db()
    brand = get_settings(conn)
    conn.close()
    return {"brand": brand}


# ---------------------------------------------------------------------------
# Archivos subidos (imágenes / logo)
# ---------------------------------------------------------------------------
@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOADS, filename)


# ---------------------------------------------------------------------------
# Dashboard / Inventario
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    conn = db()
    total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    total_sets = conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
    total_categories = conn.execute("SELECT COUNT(*) FROM categories WHERE name <> 'Conjuntos'").fetchone()[0]
    recent_products = [dict(r) for r in conn.execute(
        "SELECT * FROM products ORDER BY datetime(updated_at) DESC, id DESC LIMIT 6"
    ).fetchall()]
    recent_sets = [dict(r) for r in conn.execute(
        """SELECT s.*, COALESCE(SUM(p.cost * si.quantity),0) AS cost
           FROM sets s
           LEFT JOIN set_items si ON si.set_id=s.id
           LEFT JOIN products p ON p.id=si.product_id
           GROUP BY s.id ORDER BY s.id DESC LIMIT 6"""
    ).fetchall()]
    conn.close()
    return render_template(
        "dashboard.html",
        total_products=total_products,
        total_sets=total_sets,
        total_categories=total_categories,
        recent_products=recent_products,
        recent_sets=recent_sets,
    )


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------
def _products_with_colors(conn):
    rows = conn.execute(
        """SELECT p.*, GROUP_CONCAT(DISTINCT c.color) AS colors_list
           FROM products p LEFT JOIN colors c ON c.product_id = p.id
           GROUP BY p.id ORDER BY p.category, p.reference"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.route("/productos")
def products_list():
    conn = db()
    products = _products_with_colors(conn)
    categories = get_categories(conn, exclude_conjuntos=True)
    colors = distinct_colors(conn)
    sizes = distinct_sizes(conn)
    conn.close()
    return render_template(
        "products_list.html",
        products=products,
        categories=categories,
        colors=colors,
        sizes=sizes,
    )


@app.route("/productos/nuevo", methods=["GET", "POST"])
def product_new():
    conn = db()
    try:
        if request.method == "POST":
            reference = request.form["reference"].strip().upper()
            category = request.form["category"]
            name = request.form["name"].strip()
            description = request.form.get("description", "").strip()
            cost = float(request.form.get("cost") or 0)
            sale = float(request.form.get("sale_price") or 0)
            sizes = normalize_sizes(request.form.get("sizes", ""))
            published = 1 if request.form.get("published") else 0
            image = save_image(request.files.get("image"))
            try:
                cur = conn.execute(
                    """INSERT INTO products(reference,category,name,description,cost,sale_price,sizes,image,created_at,updated_at,published)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (reference, category, name, description, cost, sale, sizes, image, now_str(), now_str(), published),
                )
                product_id = cur.lastrowid
                colors = request.form.getlist("color")
                color_images = request.files.getlist("color_image")
                for i, color in enumerate(colors):
                    if color.strip():
                        ci = save_image(color_images[i]) if i < len(color_images) else None
                        conn.execute(
                            "INSERT INTO colors(product_id,color,image) VALUES(?,?,?)",
                            (product_id, color.strip(), ci),
                        )
                conn.commit()
                flash("Producto guardado correctamente.", "ok")
                return redirect(url_for("products_list"))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("La referencia ya existe. Usa una referencia diferente.", "error")

        categories = get_categories(conn, exclude_conjuntos=True)
        return render_template("product_form.html", product=None, colors=[], categories=categories, mode="nuevo")
    finally:
        conn.close()


@app.route("/productos/<int:pid>")
def product_detail(pid):
    conn = db()
    product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not product:
        conn.close()
        flash("El producto no existe.", "error")
        return redirect(url_for("products_list"))
    colors = [dict(r) for r in conn.execute("SELECT * FROM colors WHERE product_id=? ORDER BY id", (pid,)).fetchall()]
    conn.close()
    return render_template("product_detail.html", product=dict(product), colors=colors)


@app.route("/productos/<int:pid>/editar", methods=["GET", "POST"])
def product_edit(pid):
    conn = db()
    try:
        product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not product:
            flash("El producto no existe.", "error")
            return redirect(url_for("products_list"))

        if request.method == "POST":
            reference = request.form["reference"].strip().upper()
            category = request.form["category"]
            name = request.form["name"].strip()
            description = request.form.get("description", "").strip()
            cost = float(request.form.get("cost") or 0)
            sale = float(request.form.get("sale_price") or 0)
            sizes = normalize_sizes(request.form.get("sizes", ""))
            published = 1 if request.form.get("published") else 0

            new_image = save_image(request.files.get("image"))
            image_value = new_image if new_image else product["image"]

            try:
                conn.execute(
                    """UPDATE products SET reference=?, category=?, name=?, description=?, cost=?,
                       sale_price=?, sizes=?, image=?, updated_at=?, published=? WHERE id=?""",
                    (reference, category, name, description, cost, sale, sizes, image_value, now_str(), published, pid),
                )

                # Colores existentes: actualizar, reemplazar imagen si se sube una nueva, o eliminar
                color_ids = request.form.getlist("color_id")
                color_names = request.form.getlist("color_name")
                color_images = request.files.getlist("color_image_existing")
                color_deletes = set(request.form.getlist("color_delete"))
                for i, cid in enumerate(color_ids):
                    if cid in color_deletes:
                        conn.execute("DELETE FROM colors WHERE id=? AND product_id=?", (cid, pid))
                        continue
                    cname = color_names[i].strip() if i < len(color_names) else ""
                    if not cname:
                        continue
                    new_ci = save_image(color_images[i]) if i < len(color_images) else None
                    if new_ci:
                        conn.execute("UPDATE colors SET color=?, image=? WHERE id=? AND product_id=?", (cname, new_ci, cid, pid))
                    else:
                        conn.execute("UPDATE colors SET color=? WHERE id=? AND product_id=?", (cname, cid, pid))

                # Colores nuevos
                new_color_names = request.form.getlist("new_color")
                new_color_images = request.files.getlist("new_color_image")
                for i, cname in enumerate(new_color_names):
                    if cname.strip():
                        ci = save_image(new_color_images[i]) if i < len(new_color_images) else None
                        conn.execute(
                            "INSERT INTO colors(product_id,color,image) VALUES(?,?,?)",
                            (pid, cname.strip(), ci),
                        )

                conn.commit()
                flash("Producto actualizado correctamente.", "ok")
                return redirect(url_for("product_detail", pid=pid))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("La referencia ya existe en otro producto.", "error")

        colors = [dict(r) for r in conn.execute("SELECT * FROM colors WHERE product_id=? ORDER BY id", (pid,)).fetchall()]
        categories = get_categories(conn, exclude_conjuntos=True)
        return render_template("product_form.html", product=dict(product), colors=colors, categories=categories, mode="editar")
    finally:
        conn.close()


@app.post("/productos/<int:pid>/eliminar")
def product_delete(pid):
    conn = db()
    try:
        used_in = conn.execute(
            """SELECT DISTINCT s.reference FROM set_items si
               JOIN sets s ON s.id = si.set_id WHERE si.product_id=?""",
            (pid,),
        ).fetchall()
        if used_in:
            refs = ", ".join(r["reference"] for r in used_in)
            flash(
                f"No se puede eliminar: esta referencia hace parte del/de los conjunto(s) {refs}. "
                "Quítala de esos conjuntos primero.",
                "error",
            )
        else:
            conn.execute("DELETE FROM products WHERE id=?", (pid,))
            conn.commit()
            flash("Producto eliminado.", "ok")
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("No se pudo eliminar el producto porque tiene información relacionada.", "error")
    finally:
        conn.close()
    return redirect(url_for("products_list"))


@app.post("/productos/<int:pid>/publicar-toggle")
def product_toggle_published(pid):
    conn = db()
    try:
        row = conn.execute("SELECT published FROM products WHERE id=?", (pid,)).fetchone()
        if row is not None:
            new_val = 0 if row["published"] else 1
            conn.execute("UPDATE products SET published=? WHERE id=?", (new_val, pid))
            conn.commit()
            flash("Producto publicado en el catálogo." if new_val else "Producto oculto del catálogo.", "ok")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("products_list"))


# ---------------------------------------------------------------------------
# Conjuntos
# ---------------------------------------------------------------------------
def _sets_with_cost(conn):
    rows = conn.execute(
        """SELECT s.*, COALESCE(SUM(p.cost * si.quantity),0) AS cost
           FROM sets s
           LEFT JOIN set_items si ON si.set_id=s.id
           LEFT JOIN products p ON p.id=si.product_id
           GROUP BY s.id ORDER BY s.reference"""
    ).fetchall()
    return [dict(r) for r in rows]


def _set_items_detail(conn, sid):
    rows = conn.execute(
        """SELECT si.id AS item_id, si.quantity, p.id AS product_id, p.reference, p.name, p.cost, p.image
           FROM set_items si JOIN products p ON p.id = si.product_id
           WHERE si.set_id=? ORDER BY p.reference""",
        (sid,),
    ).fetchall()
    return [dict(r) for r in rows]


def _set_colors(conn, sid):
    rows = conn.execute("SELECT * FROM set_colors WHERE set_id=? ORDER BY id", (sid,)).fetchall()
    return [dict(r) for r in rows]


@app.route("/conjuntos")
def sets_list():
    conn = db()
    sets_ = _sets_with_cost(conn)
    conn.close()
    return render_template("sets_list.html", sets=sets_)


@app.route("/conjuntos/nuevo", methods=["GET", "POST"])
def set_new():
    conn = db()
    try:
        if request.method == "POST":
            reference = request.form["set_reference"].strip().upper()
            name = request.form["set_name"].strip()
            description = request.form.get("set_description", "").strip()
            sale = float(request.form.get("set_sale_price") or 0)
            sizes = normalize_sizes(request.form.get("set_sizes", ""))
            published = 1 if request.form.get("published") else 0
            image = save_image(request.files.get("set_image"))
            product_ids = request.form.getlist("set_product_id")
            quantities = request.form.getlist("set_quantity")
            try:
                cur = conn.execute(
                    """INSERT INTO sets(reference,name,description,sale_price,image,updated_at,sizes,published)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (reference, name, description, sale, image, now_str(), sizes, published),
                )
                sid = cur.lastrowid
                for i, pid in enumerate(product_ids):
                    if pid:
                        qty = int(quantities[i] or 1) if i < len(quantities) else 1
                        conn.execute(
                            "INSERT INTO set_items(set_id,product_id,quantity) VALUES(?,?,?)",
                            (sid, int(pid), max(1, qty)),
                        )
                colors = request.form.getlist("color")
                color_images = request.files.getlist("color_image")
                for i, color in enumerate(colors):
                    if color.strip():
                        ci = save_image(color_images[i]) if i < len(color_images) else None
                        conn.execute(
                            "INSERT INTO set_colors(set_id,color,image) VALUES(?,?,?)",
                            (sid, color.strip(), ci),
                        )
                conn.commit()
                flash("Conjunto guardado correctamente.", "ok")
                return redirect(url_for("sets_list"))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("La referencia del conjunto ya existe.", "error")

        products = _products_with_colors(conn)
        return render_template("set_form.html", set=None, items=[], colors=[], products=products, mode="nuevo")
    finally:
        conn.close()


@app.route("/conjuntos/<int:sid>")
def set_detail(sid):
    conn = db()
    s = conn.execute(
        """SELECT s.*, COALESCE(SUM(p.cost * si.quantity),0) AS cost
           FROM sets s
           LEFT JOIN set_items si ON si.set_id=s.id
           LEFT JOIN products p ON p.id=si.product_id
           WHERE s.id=? GROUP BY s.id""",
        (sid,),
    ).fetchone()
    if not s:
        conn.close()
        flash("El conjunto no existe.", "error")
        return redirect(url_for("sets_list"))
    items = _set_items_detail(conn, sid)
    colors = _set_colors(conn, sid)
    conn.close()
    return render_template("set_detail.html", s=dict(s), items=items, colors=colors)


@app.route("/conjuntos/<int:sid>/editar", methods=["GET", "POST"])
def set_edit(sid):
    conn = db()
    try:
        s = conn.execute("SELECT * FROM sets WHERE id=?", (sid,)).fetchone()
        if not s:
            flash("El conjunto no existe.", "error")
            return redirect(url_for("sets_list"))

        if request.method == "POST":
            reference = request.form["set_reference"].strip().upper()
            name = request.form["set_name"].strip()
            description = request.form.get("set_description", "").strip()
            sale = float(request.form.get("set_sale_price") or 0)
            sizes = normalize_sizes(request.form.get("set_sizes", ""))
            published = 1 if request.form.get("published") else 0
            new_image = save_image(request.files.get("set_image"))
            image_value = new_image if new_image else s["image"]
            product_ids = request.form.getlist("set_product_id")
            quantities = request.form.getlist("set_quantity")
            try:
                conn.execute(
                    """UPDATE sets SET reference=?, name=?, description=?, sale_price=?, image=?,
                       updated_at=?, sizes=?, published=? WHERE id=?""",
                    (reference, name, description, sale, image_value, now_str(), sizes, published, sid),
                )
                conn.execute("DELETE FROM set_items WHERE set_id=?", (sid,))
                for i, pid in enumerate(product_ids):
                    if pid:
                        qty = int(quantities[i] or 1) if i < len(quantities) else 1
                        conn.execute(
                            "INSERT INTO set_items(set_id,product_id,quantity) VALUES(?,?,?)",
                            (sid, int(pid), max(1, qty)),
                        )

                # Colores existentes del conjunto: actualizar, reemplazar imagen o eliminar
                color_ids = request.form.getlist("color_id")
                color_names = request.form.getlist("color_name")
                color_images = request.files.getlist("color_image_existing")
                color_deletes = set(request.form.getlist("color_delete"))
                for i, cid in enumerate(color_ids):
                    if cid in color_deletes:
                        conn.execute("DELETE FROM set_colors WHERE id=? AND set_id=?", (cid, sid))
                        continue
                    cname = color_names[i].strip() if i < len(color_names) else ""
                    if not cname:
                        continue
                    new_ci = save_image(color_images[i]) if i < len(color_images) else None
                    if new_ci:
                        conn.execute("UPDATE set_colors SET color=?, image=? WHERE id=? AND set_id=?", (cname, new_ci, cid, sid))
                    else:
                        conn.execute("UPDATE set_colors SET color=? WHERE id=? AND set_id=?", (cname, cid, sid))

                # Colores nuevos del conjunto
                new_color_names = request.form.getlist("new_color")
                new_color_images = request.files.getlist("new_color_image")
                for i, cname in enumerate(new_color_names):
                    if cname.strip():
                        ci = save_image(new_color_images[i]) if i < len(new_color_images) else None
                        conn.execute(
                            "INSERT INTO set_colors(set_id,color,image) VALUES(?,?,?)",
                            (sid, cname.strip(), ci),
                        )

                conn.commit()
                flash("Conjunto actualizado correctamente.", "ok")
                return redirect(url_for("set_detail", sid=sid))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("La referencia del conjunto ya existe en otro conjunto.", "error")

        items = _set_items_detail(conn, sid)
        colors = _set_colors(conn, sid)
        products = _products_with_colors(conn)
        return render_template("set_form.html", set=dict(s), items=items, colors=colors, products=products, mode="editar")
    finally:
        conn.close()


@app.post("/conjuntos/<int:sid>/eliminar")
def set_delete(sid):
    conn = db()
    try:
        conn.execute("DELETE FROM sets WHERE id=?", (sid,))
        conn.commit()
        flash("Conjunto eliminado.", "ok")
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("No se pudo eliminar el conjunto.", "error")
    finally:
        conn.close()
    return redirect(url_for("sets_list"))


@app.post("/conjuntos/<int:sid>/publicar-toggle")
def set_toggle_published(sid):
    conn = db()
    try:
        row = conn.execute("SELECT published FROM sets WHERE id=?", (sid,)).fetchone()
        if row is not None:
            new_val = 0 if row["published"] else 1
            conn.execute("UPDATE sets SET published=? WHERE id=?", (new_val, sid))
            conn.commit()
            flash("Conjunto publicado en el catálogo." if new_val else "Conjunto oculto del catálogo.", "ok")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("sets_list"))


# ---------------------------------------------------------------------------
# Exportar (CSV con costo / venta / ganancia, para llevarlo a Excel)
# ---------------------------------------------------------------------------
@app.route("/exportar/catalogo.csv")
def export_catalog_csv():
    conn = db()
    products = _products_with_colors(conn)
    sets_ = _sets_with_cost(conn)
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Tipo", "Referencia", "Nombre", "Categoría", "Colores", "Tallas",
        "Costo", "Precio de venta", "Ganancia", "Margen %", "Publicado",
    ])

    def add_row(tipo, reference, name, category, colors_list, sizes, cost, sale_price, published):
        cost = float(cost or 0)
        sale_price = float(sale_price or 0)
        profit = sale_price - cost
        margin = round((profit / sale_price) * 100, 1) if sale_price else 0
        writer.writerow([
            tipo, reference, name, category or "", colors_list or "", sizes or "",
            round(cost), round(sale_price), round(profit),
            margin, "Sí" if published else "No",
        ])

    for p in products:
        add_row("Producto", p["reference"], p["name"], p.get("category"), p.get("colors_list"), p.get("sizes"),
                p.get("cost"), p.get("sale_price"), p.get("published"))

    for s in sets_:
        add_row("Conjunto", s["reference"], s["name"], "Conjuntos", "", s.get("sizes"),
                s.get("cost"), s.get("sale_price"), s.get("published"))

    csv_bytes = "﻿" + buf.getvalue()  # BOM para que Excel muestre bien las tildes/ñ
    filename = "catalogo_costo_venta_" + now_str().replace(" ", "_").replace(":", "-") + ".csv"
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Registro de ventas (solo visible aquí, en el administrador — nunca en el
# catálogo público. No modifica productos/conjuntos ni su inventario.)
# ---------------------------------------------------------------------------
SHIPPING_PAYER_LABELS = {
    "ninguno": "Sin envío",
    "tienda": "Yo pago el envío",
    "cliente": "El cliente paga el envío",
}


def _sale_profit(cost, sale_price, shipping_cost, shipping_payer):
    ship = float(shipping_cost or 0) if shipping_payer == "tienda" else 0.0
    return float(sale_price or 0) - float(cost or 0) - ship


def _sales_catalog_options(conn):
    """Lista de productos y conjuntos para autocompletar la venta (solo lectura)."""
    options = []
    for p in conn.execute("SELECT reference, name, cost, sale_price FROM products ORDER BY reference"):
        options.append({"ref": p["reference"], "name": p["name"], "cost": p["cost"], "sale_price": p["sale_price"]})
    for s in _sets_with_cost(conn):
        options.append({"ref": s["reference"], "name": s["name"], "cost": s["cost"], "sale_price": s["sale_price"]})
    return options


def _sale_form_values(form):
    return {
        "sale_date": form.get("sale_date") or now_str()[:10],
        "product_ref": form.get("product_ref", "").strip(),
        "product_name": form.get("product_name", "").strip(),
        "quantity": int(form.get("quantity") or 1),
        "cost": float(form.get("cost") or 0),
        "sale_price": float(form.get("sale_price") or 0),
        "shipping_cost": float(form.get("shipping_cost") or 0),
        "shipping_payer": form.get("shipping_payer") or "ninguno",
        "customer_name": form.get("customer_name", "").strip(),
        "customer_phone": form.get("customer_phone", "").strip(),
        "customer_location": form.get("customer_location", "").strip(),
        "notes": form.get("notes", "").strip(),
    }


@app.route("/ventas")
def sales_list():
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sales ORDER BY date(sale_date) DESC, id DESC").fetchall()]
    conn.close()
    for r in rows:
        r["profit"] = _sale_profit(r["cost"], r["sale_price"], r["shipping_cost"], r["shipping_payer"])
        r["payer_label"] = SHIPPING_PAYER_LABELS.get(r["shipping_payer"], r["shipping_payer"])
    return render_template(
        "sales_list.html",
        sales=rows,
        total_sales=len(rows),
        total_revenue=sum(r["sale_price"] or 0 for r in rows),
        total_cost=sum(r["cost"] or 0 for r in rows),
        total_profit=sum(r["profit"] for r in rows),
    )


@app.route("/ventas/nueva", methods=["GET", "POST"])
def sale_new():
    conn = db()
    try:
        if request.method == "POST":
            v = _sale_form_values(request.form)
            if not v["product_name"]:
                flash("Escribe el nombre del producto vendido.", "error")
            else:
                conn.execute(
                    """INSERT INTO sales(sale_date,product_ref,product_name,quantity,cost,sale_price,
                       shipping_cost,shipping_payer,customer_name,customer_phone,customer_location,notes,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (v["sale_date"], v["product_ref"], v["product_name"], v["quantity"], v["cost"], v["sale_price"],
                     v["shipping_cost"], v["shipping_payer"], v["customer_name"], v["customer_phone"],
                     v["customer_location"], v["notes"], now_str()),
                )
                conn.commit()
                flash("Venta registrada correctamente.", "ok")
                return redirect(url_for("sales_list"))

        return render_template(
            "sale_form.html", sale=None, mode="nueva",
            catalog_options=_sales_catalog_options(conn), today=now_str()[:10],
            payer_labels=SHIPPING_PAYER_LABELS,
        )
    finally:
        conn.close()


@app.route("/ventas/<int:said>/editar", methods=["GET", "POST"])
def sale_edit(said):
    conn = db()
    try:
        sale = conn.execute("SELECT * FROM sales WHERE id=?", (said,)).fetchone()
        if not sale:
            flash("La venta no existe.", "error")
            return redirect(url_for("sales_list"))

        if request.method == "POST":
            v = _sale_form_values(request.form)
            if not v["product_name"]:
                flash("Escribe el nombre del producto vendido.", "error")
            else:
                conn.execute(
                    """UPDATE sales SET sale_date=?, product_ref=?, product_name=?, quantity=?, cost=?, sale_price=?,
                       shipping_cost=?, shipping_payer=?, customer_name=?, customer_phone=?, customer_location=?, notes=?
                       WHERE id=?""",
                    (v["sale_date"], v["product_ref"], v["product_name"], v["quantity"], v["cost"], v["sale_price"],
                     v["shipping_cost"], v["shipping_payer"], v["customer_name"], v["customer_phone"],
                     v["customer_location"], v["notes"], said),
                )
                conn.commit()
                flash("Venta actualizada correctamente.", "ok")
                return redirect(url_for("sales_list"))

        return render_template(
            "sale_form.html", sale=dict(sale), mode="editar",
            catalog_options=_sales_catalog_options(conn), today=now_str()[:10],
            payer_labels=SHIPPING_PAYER_LABELS,
        )
    finally:
        conn.close()


@app.route("/ventas/<int:said>/eliminar", methods=["POST"])
def sale_delete(said):
    conn = db()
    try:
        conn.execute("DELETE FROM sales WHERE id=?", (said,))
        conn.commit()
        flash("Venta eliminada.", "ok")
    finally:
        conn.close()
    return redirect(url_for("sales_list"))


@app.route("/ventas/exportar.csv")
def export_sales_csv():
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sales ORDER BY date(sale_date) DESC, id DESC").fetchall()]
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Fecha", "Referencia", "Producto", "Cantidad", "Costo", "Precio de venta", "Envío",
        "Quién paga el envío", "Ganancia neta", "Cliente", "Teléfono", "Ciudad/lugar", "Notas",
    ])
    for r in rows:
        profit = _sale_profit(r["cost"], r["sale_price"], r["shipping_cost"], r["shipping_payer"])
        writer.writerow([
            r["sale_date"], r["product_ref"], r["product_name"], r["quantity"],
            round(r["cost"]), round(r["sale_price"]), round(r["shipping_cost"]),
            SHIPPING_PAYER_LABELS.get(r["shipping_payer"], r["shipping_payer"]),
            round(profit), r["customer_name"], r["customer_phone"], r["customer_location"], r["notes"],
        ])

    csv_bytes = "﻿" + buf.getvalue()
    filename = "ventas_" + now_str().replace(" ", "_").replace(":", "-") + ".csv"
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Categorías
# ---------------------------------------------------------------------------
@app.route("/categorias")
def categories_page():
    conn = db()
    categories = conn.execute(
        """SELECT c.id, c.name, c.sort_order,
                  (SELECT COUNT(*) FROM products p WHERE p.category = c.name) AS product_count
           FROM categories c ORDER BY c.sort_order, c.name"""
    ).fetchall()
    conn.close()
    return render_template("categories.html", categories=[dict(r) for r in categories])


@app.post("/categorias/nueva")
def category_new():
    name = request.form.get("name", "").strip()
    conn = db()
    try:
        if not name:
            flash("Escribe un nombre de categoría.", "error")
        else:
            try:
                max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM categories").fetchone()[0]
                conn.execute("INSERT INTO categories(name, sort_order) VALUES (?, ?)", (name, max_order + 1))
                conn.commit()
                flash("Categoría agregada.", "ok")
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("Esa categoría ya existe.", "error")
    finally:
        conn.close()
    return redirect(url_for("categories_page"))


@app.post("/categorias/<int:cid>/eliminar")
def category_delete(cid):
    conn = db()
    try:
        row = conn.execute("SELECT name FROM categories WHERE id=?", (cid,)).fetchone()
        if row:
            in_use = conn.execute("SELECT COUNT(*) FROM products WHERE category=?", (row["name"],)).fetchone()[0]
            if in_use > 0:
                flash(f"No puedes eliminar '{row['name']}' porque tiene {in_use} producto(s) asignado(s).", "error")
            elif row["name"] == "Conjuntos":
                flash("La categoría 'Conjuntos' es reservada y no se puede eliminar.", "error")
            else:
                conn.execute("DELETE FROM categories WHERE id=?", (cid,))
                conn.commit()
                flash("Categoría eliminada.", "ok")
    finally:
        conn.close()
    return redirect(url_for("categories_page"))


# ---------------------------------------------------------------------------
# Configuración de marca
# ---------------------------------------------------------------------------
@app.route("/configuracion", methods=["GET", "POST"])
def settings_page():
    conn = db()
    try:
        if request.method == "POST":
            brand_name = request.form.get("brand_name", "").strip() or "sportswear_ST"
            conn.execute("INSERT INTO settings(key,value) VALUES('brand_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (brand_name,))

            primary_color = request.form.get("primary_color", "").strip()
            if primary_color:
                conn.execute("INSERT INTO settings(key,value) VALUES('primary_color',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (primary_color,))
            accent_color = request.form.get("accent_color", "").strip()
            if accent_color:
                conn.execute("INSERT INTO settings(key,value) VALUES('accent_color',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (accent_color,))

            for key in ("tagline", "short_description", "instagram_url", "whatsapp_number"):
                val = request.form.get(key, "").strip()
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, val),
                )

            logo_file = request.files.get("logo")
            if logo_file and logo_file.filename:
                logo_name = save_image(logo_file, allowed=ALLOWED_LOGO)
                if logo_name:
                    conn.execute("INSERT INTO settings(key,value) VALUES('logo',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (logo_name,))
                else:
                    flash("El logo debe ser una imagen PNG, JPG, WEBP, GIF o SVG.", "error")

            conn.commit()
            flash("Configuración guardada.", "ok")
            return redirect(url_for("settings_page"))

        brand = get_settings(conn)
        return render_template("settings.html", brand=brand)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Catálogo público (solo lectura, para compartir por enlace)
# ---------------------------------------------------------------------------
def image_url(filename):
    return url_for("uploaded_file", filename=filename) if filename else None


def public_catalog_items(conn):
    """Combina productos y conjuntos PUBLICADOS en una sola lista, lista para
    mostrarse en el catálogo público y para incrustarse como JSON (el carrito
    del cliente vive en su navegador, pero siempre resuelve nombre/precio/imagen
    desde aquí, así nunca queda desactualizado)."""
    items = []

    products = conn.execute(
        "SELECT * FROM products WHERE published=1 ORDER BY category, reference"
    ).fetchall()
    for p in products:
        colors = conn.execute("SELECT * FROM colors WHERE product_id=? ORDER BY id", (p["id"],)).fetchall()
        images = []
        if p["image"]:
            images.append(image_url(p["image"]))
        for c in colors:
            if c["image"]:
                u = image_url(c["image"])
                if u not in images:
                    images.append(u)
        items.append({
            "type": "producto",
            "ref": p["reference"],
            "name": p["name"],
            "category": p["category"],
            "description": p["description"] or "",
            "price": p["sale_price"],
            "image": image_url(p["image"]),
            "images": images,
            "colors": [{"name": c["color"], "image": image_url(c["image"])} for c in colors],
            "sizes": [t.strip() for t in (p["sizes"] or "").split(",") if t.strip()],
            "updated": p["updated_at"] or p["created_at"] or "",
            "includes": [],
            "url": url_for("catalog_product", reference=p["reference"]),
        })

    sets_ = conn.execute("SELECT * FROM sets WHERE published=1 ORDER BY reference").fetchall()
    for s in sets_:
        colors = _set_colors(conn, s["id"])
        set_items = _set_items_detail(conn, s["id"])
        images = []
        if s["image"]:
            images.append(image_url(s["image"]))
        for c in colors:
            if c["image"]:
                u = image_url(c["image"])
                if u not in images:
                    images.append(u)
        items.append({
            "type": "conjunto",
            "ref": s["reference"],
            "name": s["name"],
            "category": "Conjuntos",
            "description": s["description"] or "",
            "price": s["sale_price"],
            "image": image_url(s["image"]),
            "images": images,
            "colors": [{"name": c["color"], "image": image_url(c["image"])} for c in colors],
            "sizes": [t.strip() for t in (s["sizes"] or "").split(",") if t.strip()],
            "updated": s["updated_at"] or "",
            "includes": [{"ref": it["reference"], "name": it["name"]} for it in set_items],
            "url": url_for("catalog_product", reference=s["reference"]),
        })
    return items


@app.route("/catalogo")
def public_catalog():
    conn = db()
    try:
        items = public_catalog_items(conn)
        categories = get_categories(conn)
    finally:
        conn.close()
    return render_template("catalog_list.html", items=items, categories=categories)


@app.route("/catalogo/producto/<reference>")
def catalog_product(reference):
    conn = db()
    try:
        items = public_catalog_items(conn)
    finally:
        conn.close()
    ref_upper = reference.strip().upper()
    item = next((it for it in items if it["ref"].upper() == ref_upper), None)
    return render_template("catalog_product.html", item=item, items=items)


@app.route("/catalogo/pedido")
def catalog_pedido():
    conn = db()
    try:
        items = public_catalog_items(conn)
    finally:
        conn.close()
    return render_template("catalog_pedido.html", items=items)


@app.get("/api/catalogo/items")
def api_catalog_items():
    conn = db()
    try:
        items = public_catalog_items(conn)
    finally:
        conn.close()
    return jsonify(items)


# ---------------------------------------------------------------------------
# API auxiliar (usada por los formularios de conjuntos)
# ---------------------------------------------------------------------------
@app.get("/api/products")
def api_products():
    conn = db()
    rows = conn.execute("SELECT id,reference,category,name,cost,sale_price,sizes FROM products ORDER BY reference").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/product/<int:pid>/colors")
def api_colors(pid):
    conn = db()
    rows = conn.execute("SELECT * FROM colors WHERE product_id=? ORDER BY id", (pid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
