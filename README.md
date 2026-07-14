# Aduanex 360 — Sistema de Alta de Clientes

Prototipo en **Streamlit + Supabase**. Dos módulos:

- **Vendedor** (`/`) — captura cliente + dirección, genera clave `ANDO-###` y liga segura
- **Cliente** (`/?token=...`) — aviso de privacidad, firma y carga de INE

---

## Correr en tu computadora (paso a paso)

### 1. Instalar Python
Necesitas **Python 3.10 o superior**. Verifica:
```bash
python --version
```
Si no lo tienes: descárgalo de [python.org](https://python.org). **En Windows, marca la casilla "Add Python to PATH"** durante la instalación.

### 2. Preparar el proyecto
```bash
cd aduanex

# Entorno virtual (aísla las librerías de este proyecto)
python -m venv venv

# Activarlo:
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac / Linux

# Instalar librerías
pip install -r requirements.txt
```

### 3. Preparar Supabase

**a) Ejecutar el esquema**
Dashboard de Supabase → **SQL Editor** → **New query** → pega todo `schema.sql` → **Run**.

**b) Crear el bucket**
**Storage** → **New bucket**
- Nombre: `documentos-clientes`
- **Public: NO** ← si lo dejas público, los INE de tus clientes quedan en URLs adivinables. Es una fuga de datos.

**c) Copiar las llaves**
**Project Settings** → **API**. Necesitas:
- `Project URL`
- `service_role` key ← **la secreta, NO la `anon`**

### 4. Configurar las credenciales
```bash
cp .streamlit/secrets.toml.ejemplo .streamlit/secrets.toml
```
Abre `.streamlit/secrets.toml` y pega tus llaves.

> ⚠️ Ese archivo **nunca** se sube a GitHub. Ya está en `.gitignore`. Si la `service_role` key se filtra, alguien tiene control total de tu base de datos.

### 5. Correr
```bash
streamlit run app.py
```
Abre `http://localhost:8501`.

**Para probar el flujo completo:**
1. Entra con tu `APP_PASSWORD`
2. Captura un cliente → te da la clave `ANDO-100` y la liga
3. Copia la liga y ábrela en una **ventana de incógnito**
4. Acepta el aviso, firma, sube cualquier PDF, finaliza
5. Vuelve a abrir la misma liga → debe decir **"ya fue completado"**

---

## Publicarlo en internet (gratis)

**Streamlit Community Cloud** — gratis, y ya tienes Google AI Pro así que no necesitas nada más:

1. Sube el proyecto a un repo **privado** de GitHub (`secrets.toml` no se sube — el `.gitignore` lo bloquea)
2. Entra a [share.streamlit.io](https://share.streamlit.io) → conecta el repo
3. En **Advanced settings → Secrets**, pega el contenido de tu `secrets.toml`
4. Cambia `APP_URL` a la URL que te dé Streamlit

Tu costo mensual: **$0**.

---

## Lo que corregí de la especificación original

| Lo que pediste | El problema | Lo que hice |
|---|---|---|
| "insertar en `clientes` y `direcciones` **en la misma transacción**" | El SDK de Python **no tiene transacciones**: cada `.insert()` es un HTTP request suelto. Si el segundo falla, te queda un cliente huérfano. | Función RPC `crear_cliente_con_direccion()` en Postgres. Todo dentro de un `BEGIN...COMMIT` real. |
| Token UUID en la URL | Sin caducidad, la liga vive para siempre en el WhatsApp del cliente. | Agregué `token_expira_en` (7 días) + un solo uso + `FOR UPDATE` contra doble clic. |
| Tablas expuestas por REST | Sin RLS, cualquiera con la `anon` key (que es **pública**) hace `SELECT * FROM clientes` y se baja tu cartera. | RLS activado en las 4 tablas. El acceso pasa solo por funciones `SECURITY DEFINER` que devuelven lo mínimo. |
| Bucket de storage | Si es público, los INE quedan en URLs adivinables. | Bucket privado + URLs firmadas de 5 minutos. |
| Clave `ANDO-###` | No la mencionaste, pero era el requisito original. | `SEQUENCE` atómica + trigger de inmutabilidad. |

---

## Advertencias que sí importan

### 🔴 La firma en canvas **no tiene validez legal en México**
Es un PNG. No es NOM-151, no es e.firma, no es firma autógrafa. Sirve como **evidencia de consentimiento** (por eso guardo IP + timestamp + hash SHA-256 de la firma), pero **no sustituye la firma de un contrato**.

Si el contrato de prestación de servicios necesita fuerza legal: usa un proveedor de firma electrónica avanzada (Mifiel, Weesign) o firma física. No te confíes de este canvas.

### 🟡 La contraseña compartida no es autenticación
`APP_PASSWORD` es suficiente para el prototipo con 2–5 personas. Pero **no sabes quién capturó cada cliente**. Cuando el sistema sea crítico, cámbialo por **Supabase Auth** (un usuario por vendedor) y agrega `creado_por` a la tabla.

### 🟡 Respaldos
El free tier de Supabase **no incluye backups automáticos**. Configura un `pg_dump` semanal a tu Drive de 2 TB:
```bash
pg_dump "postgresql://postgres:[PASS]@db.[PROJ].supabase.co:5432/postgres" \
  | gzip > aduanex_$(date +%F).sql.gz
rclone copy aduanex_$(date +%F).sql.gz gdrive:Aduanex/Respaldos
```

### 🟡 Límites del free tier de Supabase
- 500 MB de base de datos (miles de clientes — te sobra)
- 1 GB de storage (~300 INE en PDF)
- **Se pausa el proyecto tras 7 días sin actividad** (se reactiva con un clic)

Cuando llegues al límite: Supabase Pro son 25 USD/mes, o mueves los archivos a tu Drive.

---

## Estructura

```
aduanex/
├── app.py                          # UI + ruteo por token
├── lib/
│   └── db.py                       # Supabase: conexión, RPC, storage
├── schema.sql                      # ⚠️ Ejecutar en Supabase primero
├── requirements.txt
├── .gitignore
└── .streamlit/
    ├── secrets.toml.ejemplo
    └── secrets.toml                # tus llaves (NO se sube a git)
```

---

## Módulo de Datos Fiscales (asesor)

Ejecuta **`schema_fiscal.sql`** en Supabase *después* de `schema.sql`.

### Por qué lo captura el asesor y no el cliente
El **régimen fiscal** y el **uso de CFDI** son claves del SAT (`601`, `626`, `G03`...) que el cliente casi nunca conoce. Si le pides que las elija, adivina — y **un régimen equivocado hace que el CFDI se rechace al timbrarlo**. El asesor las lee directo de la Constancia.

Por eso la **CSF es obligatoria** para dar de alta una razón social. No es un adorno: es la fuente de verdad contra la que se verifica lo capturado. La restricción vive en la base (`chk_csf_requiere_fiscal`), no solo en el formulario.

### Corrección al esquema original
`schema_fiscal.sql` **reemplaza** el índice `uq_doc_vigente`. El original decía "una sola CSF vigente por cliente" — está mal: si el cliente tiene 3 razones sociales, necesita **3 CSF vigentes al mismo tiempo**, una por RFC.

| Documento | Regla |
|---|---|
| INE, CONTRATO, FIRMA | Uno vigente por **cliente** |
| CSF | Uno vigente por **razón social** |

### Comportamiento
- El formulario filtra los regímenes según el RFC: 12 caracteres → persona moral, 13 → física
- La primera razón social se marca **principal** automáticamente
- Al marcar otra como principal, la anterior se desmarca **en la misma transacción** (nunca hay dos, nunca hay cero)
- Reemplazar una CSF **versiona**: la anterior pasa a `vigente = FALSE`, no se borra
- Las CSF se ven con **URL firmada de 5 minutos** (el bucket es privado)
