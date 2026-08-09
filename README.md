# 🏦 Finanzas Bot — 3 Agentes (Personal, Hogar, Andrea)

Bot determinista de finanzas para WhatsApp (sin LLM) que registra gastos/ingresos en Google Sheets, gestiona presupuestos, inventario, frecuencia de compras y sube recibos a Mega.

## 🏗 Arquitectura (3 Agentes)

| Agente | Grupo WhatsApp | Hoja Google Sheets | Usuario |
|--------|----------------|-------------------|---------|
| **Personal** | `120363426559924341@g.us` | `14OPB7X4V4QL6RE20zqMoWztNoGEFGHDLUwk3u2zEQho` | U1 |
| **Hogar** | `120363426158712224@g.us` | `1WJMPeSNTlPzKF5TU2EljiwXU4d_O54CQpA1aJvatduM` | U2 |
| **Andrea** | `120363429174326751@g.us` | `1GQt6_AKWOp_GNKg2PAo0P-XObVPcekV2HyyZBuSa_iY` | U2 |

## 🚀 Inicio rápido

```bash
# 1. Clonar
git clone <repo> finanzas-bot
cd finanzas-bot

# 2. Configurar credenciales (una vez)
cp scripts/google_token.json.example scripts/google_token.json
# Editar con tus credenciales OAuth de Google Sheets

# 3. Mega (recibos)
cp scripts/mega_config.json.example scripts/mega_config.json
# Editar email/contraseña Mega

# 3. Instalar deps
pip install -r requirements.txt

# 4. Ejecutar gateway (WhatsApp bridge)
systemctl start hermes-gateway
```

## 📁 Estructura

```
finanzas-bot/
├── scripts/                 # Código principal (gasto.py)
│   ├── gasto.py            # Motor principal
│   ├── adapter_whatsapp.py # Bridge WhatsApp
│   ├── aprendices.json     # Palabras aprendidas (global)
│   ├── cola_aprendizaje.json # Cola admin
│   ├── mega_config.json    # Config Mega
│   └── *.json              # Estado persistente
├── adapter_whatsapp.py     # Bridge WhatsApp (copia)
├── agents/
│   ├── personal/
│   ├── hogar/
│   └── andrea/
├── docs/
└── requirements.txt
```

## 💬 Comandos de usuario

| Comando | Descripción |
|---------|-------------|
| `pague 5000 mercado por nequi` | Registra gasto |
| `recibí 500000 salario` | Registra ingreso |
| `ayuda` / `menu` | Ver guía completa |
| `gastos de agosto` | Resumen mensual |
| `presupuesto de comida 600 mil` | Define presupuesto |
| `estado de presupuestos` | Ver alertas |
| `cada cuánto compro arroz` | Frecuencia |
| `borra la última entrada` | Elimina último (con confirmación) |

## 🛠 Admin

| Comando | Acción |
|---------|--------|
| `revisar` | Ver cola de aprendizaje pendiente |
| `xaran = medicamentos` | Enseña palabra al bot (acepta: `categoría`, `subcategoría`, número 1-12) |
| `revisar` (sin args) | Lista cola pendiente |

## 🧠 Aprendizaje (Modelo Híbrido 3 Niveles)

1. **Remitente** → Bot pregunta "¿X a qué categoría?" → 1 toque → aprende
2. **No sé** → entra a `cola_aprendizaje.json`
3. **Admin** → `revisar` → ve cola → `palabra = categoría` → aprende global

## 📊 Datos persistentes

| Archivo | Contenido |
|---------|-----------|
| `aprendizajes.json` | `palabra -> {cat, sub}` global |
| `cola_aprendizaje.json` | Palabras pendientes de admin |
| `presupuestos.json` | Límites por grupo+categoría |
| `inventario.json` | Compras por producto (frecuencia) |
| `historial.jsonl` | Log de todos los eventos |

## 🔧 Requisitos

- Python 3.10+
- Google Sheets API habilitado + `google_token.json`
- Cuenta Mega (email/pass) para recibos
- WhatsApp bridge (hermes-gateway) corriendo

## 🔄 Despliegue

```bash
# Reiniciar gateway tras cambios
sudo systemctl restart hermes-gateway

# Ver logs
journalctl -u hermes-gateway -f
```

## 📄 Licencia

Uso interno - Finanzas Personales / Hogar / Andrea
EOF

# 7. requirements.txt
cat > requirements.txt << 'REQEOF'
google-api-python-client>=2.100
google-auth>=2.22
google-auth-oauthlib>=1.0
google-auth-httplib2>=0.1
mega.py>=1.0
pillow>=10
pytesseract>=0.3
python-dateutil>=2.8
requests>=2.31
PyYAML>=6.0
REQEOF

# 8. Ejemplo de configs (sin secretos)
cat > scripts/google_token.json.example << 'GTOKEN'
{
  "client_id": "TU_CLIENT_ID.apps.googleusercontent.com",
  "client_secret": "TU_CLIENT_SECRET",
  "refresh_token": "TU_REFRESH_TOKEN",
  "token_uri": "https://oauth2.googleapis.com/token",
  "scopes": ["https://www.googleapis.com/auth/spreadsheets"]
}
GTOKEN

cat > scripts/mega_config.json.example << 'MEGA'
{
  "email": "tu_email@mega.nz",
  "password": "tu_password",
  "folder": "8sRR0Z7Y"
}
MEGA

# 9. Estructura.json para referencia
cat > estructura.json << 'ESTRUCT'
{
  "agentes": {
    "personal": {"sheet": "14OPB7X4V4QL6RE20zqMoWztNoGEFGHDLUwk3u2zEQho", "grupo": "G1", "usuario": "U1"},
    "hogar": {"sheet": "1WJMPeSNTlPzKF5TU2EljiwXU4d_O54CQpA1aJvatduM", "grupo": "G2", "usuario": "U2"},
    "andrea": {"sheet": "1GQt6_AKWOp_GNKg2PAo0P-XObVPcekV2HyyZBuSa_iY", "grupo": "G1", "usuario": "U2"}
  },
  "columnas_hoja": ["id","fecha","hora","grupo","usuario","tipo","monto","moneda","categoria","subcategoria","descripcion_orig","descripcion_norm","metodo","evidencia","estado","prioridad"],
  "categorias": ["Alimentacion","Vivienda","Transporte","Salud","Tecnologia","Educacion","Ocio","Ropa","Mascotas","Ingreso"],
  "subcategorias_por_categoria": {
    "Alimentacion": ["Mercado / plaza","Restaurante / comida fuera","Domicilios","Bebidas / snacks"],
    "Vivienda": ["Arriendo","Servicios publicos","Internet / telefono","Aseo y hogar"],
    "Transporte": ["Transporte publico / bus","Gasolina / combustible","Parqueadero / peaje","Mantenimiento vehiculo"],
    "Salud": ["Medicamentos","Consulta medica / EPS","Gimnasio / bienestar"],
    "Tecnologia": ["Componentes / electronica","Suscripciones digitales","Equipos / dispositivos"],
    "Educacion": ["Cursos / certificaciones","Libros / materiales","Tesis / universidad"],
    "Ocio": ["Entretenimiento / streaming","Salidas / reuniones","Viajes"],
    "Ropa": ["Ropa y accesorios"],
    "Mascotas": ["Alimento / veterinario"],
    "Ingreso": ["Salario / nomina","Freelance / proyecto","Otros ingresos"]
  }
}
ESTRUCT

# 10. Commit inicial
git add .
git commit -m "feat: initial commit - Finanzas Bot 3 agentes (Personal, Hogar, Andrea)

- Motor gasto.py con checklist de datos + aprendizaje híbrido
- Menú ayuda + cola aprendizaje admin (revisar / palabra = cat)
- 3 agentes: Personal, Hogar, Andrea con hojas separadas
- Presupuestos, inventario, frecuencia, Mega recibos
- Docs, requirements, configs de ejemplo"

echo '✅ Repositorio creado en /home/soporte/finanzas-bot'
