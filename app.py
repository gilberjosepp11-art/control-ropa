import os
import uuid
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import requests

app = Flask(__name__)
DB_NAME = "ropa.db"

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'avif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                categoria TEXT,
                costo REAL NOT NULL,
                precio REAL NOT NULL,
                imagen TEXT
            )
        ''')
        cols = [c['name'] for c in conn.execute('PRAGMA table_info(productos)').fetchall()]
        if 'imagen' not in cols:
            conn.execute("ALTER TABLE productos ADD COLUMN imagen TEXT")

        conn.execute('''
            CREATE TABLE IF NOT EXISTS inventario (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id INTEGER NOT NULL,
                talla TEXT NOT NULL,
                stock INTEGER NOT NULL DEFAULT 0,
                stock_minimo INTEGER NOT NULL DEFAULT 2,
                FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE CASCADE
            )
        ''')
        inv_cols = [c['name'] for c in conn.execute('PRAGMA table_info(inventario)').fetchall()]
        if 'stock_minimo' not in inv_cols:
            conn.execute("ALTER TABLE inventario ADD COLUMN stock_minimo INTEGER NOT NULL DEFAULT 2")

        conn.execute('''
            CREATE TABLE IF NOT EXISTS ventas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inventario_id INTEGER NOT NULL,
                producto_nombre TEXT NOT NULL,
                talla TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                precio_cobrado REAL NOT NULL,
                ganancia REAL NOT NULL,
                metodo_pago TEXT NOT NULL DEFAULT 'Efectivo USD',
                tasa_momento REAL NOT NULL DEFAULT 1.0,
                fecha TEXT NOT NULL,
                FOREIGN KEY (inventario_id) REFERENCES inventario (id)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS creditos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_nombre TEXT NOT NULL,
                cliente_telefono TEXT,
                inventario_id INTEGER NOT NULL,
                producto_nombre TEXT NOT NULL,
                talla TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                total_deuda REAL NOT NULL,
                total_abonado REAL NOT NULL DEFAULT 0.0,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE',
                fecha_inicio TEXT NOT NULL,
                FOREIGN KEY (inventario_id) REFERENCES inventario (id)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS abonos_credito (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credito_id INTEGER NOT NULL,
                monto REAL NOT NULL,
                metodo_pago TEXT NOT NULL,
                tasa_momento REAL NOT NULL,
                fecha TEXT NOT NULL,
                FOREIGN KEY (credito_id) REFERENCES creditos (id) ON DELETE CASCADE
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS gastos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                descripcion TEXT NOT NULL,
                categoria TEXT NOT NULL,
                monto_usd REAL NOT NULL,
                metodo_pago TEXT NOT NULL,
                tasa_momento REAL NOT NULL,
                fecha TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS configuracion (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            )
        ''')
        tasa_actual = conn.execute('SELECT valor FROM configuracion WHERE clave = "tasa_usd"').fetchone()
        if not tasa_actual:
            conn.execute('INSERT INTO configuracion (clave, valor) VALUES ("tasa_usd", "800.00")')

        conn.commit()

init_db()

def consultar_bcv_online():
    try:
        r = requests.get('https://ve.dolarapi.com/v1/dolares/oficial', timeout=4)
        if r.status_code == 200:
            data = r.json()
            promedio = data.get('promedio') or data.get('price')
            if promedio:
                return float(promedio)
    except Exception:
        pass
    try:
        r = requests.get('https://rates.dolarvzla.com/bcv/current.json', timeout=4)
        if r.status_code == 200:
            data = r.json()
            usd = data.get('usd') or data.get('current', {}).get('usd')
            if usd:
                return float(usd)
    except Exception:
        pass
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/tasa/auto', methods=['POST'])
def auto_sync_bcv():
    tasa_bcv = consultar_bcv_online()
    if tasa_bcv:
        with get_db() as conn:
            conn.execute('INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ("tasa_usd", ?)', (str(tasa_bcv),))
            conn.commit()
        return jsonify({"success": True, "tasa": tasa_bcv})
    return jsonify({"success": False, "message": "No se pudo conectar con el BCV"}), 503

@app.route('/api/tasa', methods=['GET', 'POST'])
def manage_tasa():
    with get_db() as conn:
        if request.method == 'POST':
            nueva_tasa = float(request.json.get('tasa', 1.0))
            conn.execute('INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ("tasa_usd", ?)', (str(nueva_tasa),))
            conn.commit()
            return jsonify({"success": True, "tasa": nueva_tasa})
        else:
            row = conn.execute('SELECT valor FROM configuracion WHERE clave = "tasa_usd"').fetchone()
            tasa = float(row['valor']) if row else 1.0
            return jsonify({"tasa": tasa})

@app.route('/api/productos/lista', methods=['GET'])
def get_productos_lista():
    with get_db() as conn:
        prods = conn.execute('SELECT id, nombre, categoria, costo, precio FROM productos ORDER BY nombre ASC').fetchall()
        return jsonify([dict(p) for p in prods])

@app.route('/api/inventario', methods=['GET'])
def get_inventario():
    with get_db() as conn:
        items = conn.execute('''
            SELECT i.id AS item_id, p.id AS producto_id, p.nombre, p.categoria, 
                   p.costo, p.precio, p.imagen, i.talla, i.stock, i.stock_minimo
            FROM inventario i
            JOIN productos p ON i.producto_id = p.id
            ORDER BY p.nombre ASC, i.talla ASC
        ''').fetchall()
        return jsonify([dict(row) for row in items])

@app.route('/api/productos', methods=['POST'])
def add_producto():
    nombre = request.form.get('nombre', '').strip()
    categoria = request.form.get('categoria', 'General').strip()
    costo = float(request.form.get('costo', 0))
    precio = float(request.form.get('precio', 0))
    talla = request.form.get('talla', '').strip().upper()
    stock = int(request.form.get('stock', 0))
    stock_min = int(request.form.get('stock_minimo', 2))

    imagen_nombre = None
    if 'foto' in request.files:
        file = request.files['foto']
        if file and file.filename != '' and allowed_file(file.filename):
            imagen_nombre = f"{uuid.uuid4().hex[:10]}_{secure_filename(file.filename)}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_nombre))

    with get_db() as conn:
        prod = conn.execute('SELECT id, imagen FROM productos WHERE LOWER(nombre) = LOWER(?)', (nombre,)).fetchone()
        if prod:
            prod_id = prod['id']
            foto_final = imagen_nombre if imagen_nombre else prod['imagen']
            conn.execute('UPDATE productos SET costo = ?, precio = ?, imagen = ? WHERE id = ?', 
                         (costo, precio, foto_final, prod_id))
        else:
            cur = conn.execute('INSERT INTO productos (nombre, categoria, costo, precio, imagen) VALUES (?, ?, ?, ?, ?)',
                               (nombre, categoria, costo, precio, imagen_nombre))
            prod_id = cur.lastrowid

        var = conn.execute('SELECT id, stock FROM inventario WHERE producto_id = ? AND talla = ?',
                           (prod_id, talla)).fetchone()
        if var:
            conn.execute('UPDATE inventario SET stock = stock + ?, stock_minimo = ? WHERE id = ?', 
                         (stock, stock_min, var['id']))
        else:
            conn.execute('INSERT INTO inventario (producto_id, talla, stock, stock_minimo) VALUES (?, ?, ?, ?)',
                         (prod_id, talla, stock, stock_min))
        conn.commit()
    return jsonify({"success": True})

@app.route('/api/inventario/reabastecer', methods=['POST'])
def reabastecer_stock():
    data = request.json
    producto_id = int(data.get('producto_id'))
    talla = data.get('talla', '').strip().upper()
    cantidad = int(data.get('cantidad', 0))
    nuevo_costo = float(data.get('costo', 0))
    nuevo_precio = float(data.get('precio', 0))
    stock_min = int(data.get('stock_minimo', 2))

    if cantidad <= 0:
        return jsonify({"success": False, "message": "Cantidad inválida"}), 400

    with get_db() as conn:
        if nuevo_costo > 0 or nuevo_precio > 0:
            conn.execute('UPDATE productos SET costo = COALESCE(NULLIF(?, 0), costo), precio = COALESCE(NULLIF(?, 0), precio) WHERE id = ?',
                         (nuevo_costo, nuevo_precio, producto_id))

        var = conn.execute('SELECT id FROM inventario WHERE producto_id = ? AND talla = ?', (producto_id, talla)).fetchone()
        if var:
            conn.execute('UPDATE inventario SET stock = stock + ?, stock_minimo = ? WHERE id = ?', 
                         (cantidad, stock_min, var['id']))
        else:
            conn.execute('INSERT INTO inventario (producto_id, talla, stock, stock_minimo) VALUES (?, ?, ?, ?)',
                         (producto_id, talla, cantidad, stock_min))
        conn.commit()

    return jsonify({"success": True})

@app.route('/api/inventario/eliminar', methods=['POST'])
def eliminar_inventario():
    item_id = int(request.json.get('item_id'))

    with get_db() as conn:
        item = conn.execute('SELECT producto_id FROM inventario WHERE id = ?', (item_id,)).fetchone()
        if not item:
            return jsonify({"success": False, "message": "No encontrado"}), 404

        producto_id = item['producto_id']
        conn.execute('DELETE FROM inventario WHERE id = ?', (item_id,))

        restantes = conn.execute('SELECT COUNT(*) AS total FROM inventario WHERE producto_id = ?', (producto_id,)).fetchone()
        if restantes['total'] == 0:
            prod = conn.execute('SELECT imagen FROM productos WHERE id = ?', (producto_id,)).fetchone()
            if prod and prod['imagen']:
                ruta = os.path.join(app.config['UPLOAD_FOLDER'], prod['imagen'])
                if os.path.exists(ruta):
                    try:
                        os.remove(ruta)
                    except Exception:
                        pass
            conn.execute('DELETE FROM productos WHERE id = ?', (producto_id,))
        conn.commit()
    return jsonify({"success": True})

@app.route('/api/vender', methods=['POST'])
def vender():
    data = request.json
    item_id = int(data.get('item_id'))
    cantidad = int(data.get('cantidad', 1))
    tipo_operacion = data.get('tipo_operacion', 'CONTADO')

    with get_db() as conn:
        tasa_row = conn.execute('SELECT valor FROM configuracion WHERE clave = "tasa_usd"').fetchone()
        tasa_actual = float(tasa_row['valor']) if tasa_row else 1.0

        item = conn.execute('''
            SELECT i.id, i.stock, i.talla, p.nombre, p.costo, p.precio 
            FROM inventario i 
            JOIN productos p ON i.producto_id = p.id 
            WHERE i.id = ?
        ''', (item_id,)).fetchone()

        if not item or item['stock'] < cantidad:
            return jsonify({"success": False, "message": "Stock insuficiente"}), 400

        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute('UPDATE inventario SET stock = stock - ? WHERE id = ?', (cantidad, item_id))

        if tipo_operacion == 'CREDITO':
            cliente = data.get('cliente_nombre', '').strip()
            telefono = data.get('cliente_telefono', '').strip()
            total_deuda = item['precio'] * cantidad
            abono_inicial = float(data.get('abono_inicial', 0.0))
            metodo_abono = data.get('metodo_abono', 'Efectivo USD')

            cur = conn.execute('''
                INSERT INTO creditos (cliente_nombre, cliente_telefono, inventario_id, producto_nombre, talla, cantidad, total_deuda, total_abonado, estado, fecha_inicio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (cliente, telefono, item_id, item['nombre'], item['talla'], cantidad, total_deuda, abono_inicial, 
                  'PAGADO' if abono_inicial >= total_deuda else 'PENDIENTE', fecha_actual))
            
            credito_id = cur.lastrowid
            if abono_inicial > 0:
                conn.execute('''
                    INSERT INTO abonos_credito (credito_id, monto, metodo_pago, tasa_momento, fecha)
                    VALUES (?, ?, ?, ?, ?)
                ''', (credito_id, abono_inicial, metodo_abono, tasa_actual, fecha_actual))
        else:
            metodo = data.get('metodo_pago', 'Efectivo USD').strip()
            ganancia_total = (item['precio'] - item['costo']) * cantidad
            conn.execute('''
                INSERT INTO ventas (inventario_id, producto_nombre, talla, cantidad, precio_cobrado, ganancia, metodo_pago, tasa_momento, fecha)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (item_id, item['nombre'], item['talla'], cantidad, item['precio'], ganancia_total, metodo, tasa_actual, fecha_actual))
        
        conn.commit()
    return jsonify({"success": True})

@app.route('/api/creditos', methods=['GET'])
def get_creditos():
    with get_db() as conn:
        creditos = conn.execute('''
            SELECT id, cliente_nombre, cliente_telefono, producto_nombre, talla, cantidad,
                   total_deuda, total_abonado, (total_deuda - total_abonado) AS saldo_pendiente,
                   estado, fecha_inicio
            FROM creditos
            ORDER BY CASE WHEN estado = 'PENDIENTE' THEN 1 ELSE 2 END, id DESC
        ''').fetchall()
        return jsonify([dict(c) for c in creditos])

@app.route('/api/creditos/abonar', methods=['POST'])
def abonar_credito():
    data = request.json
    credito_id = int(data.get('credito_id'))
    monto = float(data.get('monto', 0.0))
    metodo = data.get('metodo_pago', 'Efectivo USD')

    if monto <= 0:
        return jsonify({"success": False, "message": "Monto inválido"}), 400

    with get_db() as conn:
        tasa_row = conn.execute('SELECT valor FROM configuracion WHERE clave = "tasa_usd"').fetchone()
        tasa_actual = float(tasa_row['valor']) if tasa_row else 1.0
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        credito = conn.execute('SELECT total_deuda, total_abonado FROM creditos WHERE id = ?', (credito_id,)).fetchone()
        if not credito:
            return jsonify({"success": False, "message": "No encontrado"}), 404

        nuevo_abonado = credito['total_abonado'] + monto
        nuevo_estado = 'PAGADO' if nuevo_abonado >= credito['total_deuda'] else 'PENDIENTE'

        conn.execute('UPDATE creditos SET total_abonado = ?, estado = ? WHERE id = ?', 
                     (nuevo_abonado, nuevo_estado, credito_id))
        conn.execute('''
            INSERT INTO abonos_credito (credito_id, monto, metodo_pago, tasa_momento, fecha)
            VALUES (?, ?, ?, ?, ?)
        ''', (credito_id, monto, metodo, tasa_actual, fecha_actual))
        conn.commit()
    return jsonify({"success": True})

@app.route('/api/ventas/anular', methods=['POST'])
def anular_venta():
    venta_id = int(request.json.get('venta_id'))
    with get_db() as conn:
        venta = conn.execute('SELECT * FROM ventas WHERE id = ?', (venta_id,)).fetchone()
        if not venta:
            return jsonify({"success": False, "message": "No encontrada"}), 404

        conn.execute('UPDATE inventario SET stock = stock + ? WHERE id = ?', 
                     (venta['cantidad'], venta['inventario_id']))
        conn.execute('DELETE FROM ventas WHERE id = ?', (venta_id,))
        conn.commit()
    return jsonify({"success": True})

@app.route('/api/gastos', methods=['GET', 'POST'])
def manage_gastos():
    with get_db() as conn:
        if request.method == 'POST':
            data = request.json
            desc = data.get('descripcion', '').strip()
            cat = data.get('categoria', 'General').strip()
            monto = float(data.get('monto_usd', 0.0))
            metodo = data.get('metodo_pago', 'Efectivo USD').strip()
            fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            tasa_row = conn.execute('SELECT valor FROM configuracion WHERE clave = "tasa_usd"').fetchone()
            tasa_actual = float(tasa_row['valor']) if tasa_row else 1.0

            conn.execute('''
                INSERT INTO gastos (descripcion, categoria, monto_usd, metodo_pago, tasa_momento, fecha)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (desc, cat, monto, metodo, tasa_actual, fecha_actual))
            conn.commit()
            return jsonify({"success": True})
        else:
            gastos = conn.execute('SELECT * FROM gastos ORDER BY id DESC LIMIT 30').fetchall()
            return jsonify([dict(g) for g in gastos])

@app.route('/api/gastos/eliminar', methods=['POST'])
def eliminar_gasto():
    gasto_id = int(request.json.get('gasto_id'))
    with get_db() as conn:
        conn.execute('DELETE FROM gastos WHERE id = ?', (gasto_id,))
        conn.commit()
    return jsonify({"success": True})

@app.route('/api/metricas', methods=['GET'])
def get_metricas():
    hoy = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        resumen_contado = conn.execute('''
            SELECT 
                COALESCE(SUM(cantidad), 0) AS prendas,
                COALESCE(SUM(precio_cobrado * cantidad), 0) AS total_ingresos,
                COALESCE(SUM(ganancia), 0) AS ganancia_bruta
            FROM ventas
            WHERE fecha LIKE ?
        ''', (f'{hoy}%',)).fetchone()

        resumen_abonos = conn.execute('''
            SELECT COALESCE(SUM(monto), 0) AS total_abonos
            FROM abonos_credito
            WHERE fecha LIKE ?
        ''', (f'{hoy}%',)).fetchone()

        resumen_gastos = conn.execute('''
            SELECT COALESCE(SUM(monto_usd), 0) AS total_gastos
            FROM gastos
            WHERE fecha LIKE ?
        ''', (f'{hoy}%',)).fetchone()

        por_cobrar = conn.execute('''
            SELECT COALESCE(SUM(total_deuda - total_abonado), 0) AS total_pendiente
            FROM creditos
            WHERE estado = 'PENDIENTE'
        ''').fetchone()

        alertas = conn.execute('''
            SELECT i.id AS item_id, p.id AS producto_id, p.nombre, p.imagen, i.talla, i.stock, i.stock_minimo, p.costo, p.precio
            FROM inventario i
            JOIN productos p ON i.producto_id = p.id
            WHERE i.stock <= i.stock_minimo
            ORDER BY i.stock ASC, p.nombre ASC
        ''').fetchall()

        desglose_contado = conn.execute('''
            SELECT metodo_pago, SUM(precio_cobrado * cantidad) AS total_usd, SUM(precio_cobrado * cantidad * tasa_momento) AS total_bs
            FROM ventas WHERE fecha LIKE ? GROUP BY metodo_pago
        ''', (f'{hoy}%',)).fetchall()

        desglose_abonos = conn.execute('''
            SELECT metodo_pago, SUM(monto) AS total_usd, SUM(monto * tasa_momento) AS total_bs
            FROM abonos_credito WHERE fecha LIKE ? GROUP BY metodo_pago
        ''', (f'{hoy}%',)).fetchall()

        totales_metodo = {}
        for d in desglose_contado:
            m = d['metodo_pago']
            totales_metodo[m] = {'usd': d['total_usd'], 'bs': d['total_bs']}
        for a in desglose_abonos:
            m = a['metodo_pago']
            if m in totales_metodo:
                totales_metodo[m]['usd'] += a['total_usd']
                totales_metodo[m]['bs'] += a['total_bs']
            else:
                totales_metodo[m] = {'usd': a['total_usd'], 'bs': a['total_bs']}

        desglose_final = [{'metodo_pago': k, 'total_usd': v['usd'], 'total_bs': v['bs']} for k, v in totales_metodo.items()]

        ultimas_ventas = conn.execute('''
            SELECT id, producto_nombre, talla, cantidad, precio_cobrado, ganancia, metodo_pago, tasa_momento, fecha
            FROM ventas ORDER BY id DESC LIMIT 10
        ''').fetchall()

        ingresos_totales_hoy = resumen_contado['total_ingresos'] + resumen_abonos['total_abonos']
        gastos_hoy = resumen_gastos['total_gastos']
        ganancia_liquida_hoy = resumen_contado['ganancia_bruta'] - gastos_hoy

        return jsonify({
            "resumen_hoy": {
                "total_prendas": resumen_contado['prendas'],
                "total_ingresos": ingresos_totales_hoy,
                "total_gastos": gastos_hoy,
                "ganancia_neta": ganancia_liquida_hoy,
                "total_por_cobrar": por_cobrar['total_pendiente']
            },
            "alertas_reposicion": [dict(a) for a in alertas],
            "desglose_pagos": desglose_final,
            "ventas_recientes": [dict(v) for v in ultimas_ventas]
        })

if __name__ == '__main__':
    app.run(debug=True, port=5000)