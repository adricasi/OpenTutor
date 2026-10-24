"""Writes the synthetic Spanish Excel pivot-table AX fixture next to this file (excel_pivot_es.json).

    uv run python apps/server/tests/fixtures_screen/make_excel_pivot_es.py

Synthetic: element roles, titles and frames follow Excel for Mac 16 in Spanish (the ribbon tab "Insertar" sits at
(57, 71, 74×26), as read by AX on a real Mac in Phase 11); everything else is made up. Replace with a recording
from ink-tutor/tools/screen-recorder once it exists.
"""

import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("excel_pivot_es.json")
WIN = {"x": 0, "y": 25, "w": 1512, "h": 940}
WT = "Ventas 2026.xlsx"


def el(key, role, title=None, x=0, y=0, w=10, h=10, value=None, path=(), enabled=True):
    e = {"id": key, "role": role, "frame": {"x": x, "y": y, "w": w, "h": h}, "path": list(path)}
    if not enabled:
        e["enabled"] = False
    if title is not None:
        e["title"] = title
    if value is not None:
        e["value"] = value
    return e


W = f"AXWindow:{WT}"
TABS = ["Inicio", "Insertar", "Dibujar", "Disposición de página", "Fórmulas", "Datos", "Revisar", "Vista"]
TAB_W = {
    "Inicio": 46,
    "Insertar": 74,
    "Dibujar": 62,
    "Disposición de página": 150,
    "Fórmulas": 72,
    "Datos": 56,
    "Revisar": 66,
    "Vista": 50,
}
HOME = [
    "Pegar",
    "Cortar",
    "Copiar",
    "Copiar formato",
    "Negrita",
    "Cursiva",
    "Subrayado",
    "Bordes",
    "Color de relleno",
    "Color de fuente",
    "Alinear a la izquierda",
    "Centrar",
    "Alinear a la derecha",
    "Combinar y centrar",
    "Ajustar texto",
    "Formato de número",
    "Formato condicional",
    "Dar formato como tabla",
    "Estilos de celda",
    "Insertar",
    "Eliminar",
    "Formato",
    "Autosuma",
    "Rellenar",
    "Borrar",
    "Ordenar y filtrar",
    "Buscar y seleccionar",
    "Analizar datos",
]
INSERT = [
    "Tabla dinámica",
    "Tablas dinámicas recomendadas",
    "Tabla",
    "Imágenes",
    "Formas",
    "Iconos",
    "Modelos 3D",
    "SmartArt",
    "Captura",
    "Complementos",
    "Gráficos recomendados",
    "Columna",
    "Línea",
    "Circular",
    "Barra",
    "Área",
    "Dispersión",
    "Mapas",
    "Gráfico dinámico",
    "Minigráfico de línea",
    "Segmentación de datos",
    "Escala de tiempo",
    "Vínculo",
    "Comentario nuevo",
    "Cuadro de texto",
    "Encabezado y pie de página",
    "WordArt",
    "Ecuación",
    "Símbolo",
]
PIVOT_TABS = ["Analizar tabla dinámica", "Diseño"]


def chrome(selected, extra_tabs=()):
    out = [el("win", "AXWindow", WT, **WIN, path=())]
    for i, t in enumerate(["Guardar", "Deshacer", "Rehacer"]):
        out.append(el(f"qa{i}", "AXButton", t, 80 + 28 * i, 31, 24, 22, path=[W, "AXToolbar:"]))
    x = 8
    for t in [*TABS, *extra_tabs]:
        w = TAB_W.get(t, 9 * len(t))
        out.append(
            el(
                f"tab-{t}",
                "AXRadioButton",
                t,
                x,
                71,
                w,
                26,
                value="1" if t == selected else "0",
                path=[W, "AXTabGroup:"],
            )
        )
        x += w + 3
    names = (
        HOME
        if selected == "Inicio"
        else INSERT
        if selected == "Insertar"
        else [
            "Campos de tabla dinámica (botón)",
            "Actualizar",
            "Cambiar origen de datos",
            "Borrar",
            "Seleccionar",
            "Mover tabla dinámica",
        ]
    )
    for i, t in enumerate(names):
        role = "AXMenuButton" if t in ("Insertar", "Eliminar", "Formato", "Pegar", "Gráfico dinámico") else "AXButton"
        out.append(
            el(
                f"rb-{selected}-{i}",
                role,
                t,
                8 + 50 * (i % 30),
                102 + 40 * (i // 30),
                46,
                36,
                path=[W, "AXGroup:Cinta"],
            )
        )
    out.append(el("namebox", "AXTextField", "Cuadro de nombres", 8, 180, 90, 22, value="A1", path=[W]))
    out.append(el("fbar", "AXTextField", "Barra de fórmulas", 130, 180, 900, 22, value="Región", path=[W]))
    return out


def sheet(name, rows, cols, data=None):
    out = [el(f"tbl-{name}", "AXTable", name, 0, 210, 1180, 690, path=[W])]
    for r in range(1, rows + 1):
        for c in range(cols):
            col = "ABCDEF"[c]
            v = data(r, c) if data else ""
            out.append(
                el(
                    f"{name}-{col}{r}",
                    "AXCell",
                    f"{col}{r}",
                    40 + 100 * c,
                    210 + 17 * r,
                    100,
                    17,
                    value=v,
                    path=[W, f"AXTable:{name}"],
                )
            )
    out.append(el(f"st-{name}", "AXRadioButton", name, 40, 915, 60, 22, value="1", path=[W, "AXTabGroup:Hojas"]))
    out.append(el("newsheet", "AXButton", "Nueva hoja", 104, 915, 22, 22, path=[W, "AXTabGroup:Hojas"]))
    out.append(el("status", "AXStaticText", "Listo", 8, 942, 60, 18, path=[W]))
    return out


REG = ["Norte", "Sur", "Este", "Oeste"]
PROD = ["Café", "Té", "Cacao"]


def data(r, c):
    if r == 1:
        return ["Región", "Producto", "Fecha", "Ventas", "", ""][c]
    if r > 21 or c > 3:
        return ""
    return [REG[r % 4], PROD[r % 3], f"2026-0{1 + r % 9}-1{r % 9}", str(100 + 37 * r % 500)][c]


def snap(elements, t_ms, window_title=WT, pointer=(900, 520), app="Microsoft Excel", **kw):
    s = {
        "type": "ax.snapshot",
        "app": app,
        "bundle_id": "com.microsoft.Excel" if app == "Microsoft Excel" else "com.apple.finder",
        "window_title": window_title,
        "display": 1,
        "elements": elements,
        "t_ms": t_ms,
        "pointer": {"x": pointer[0], "y": pointer[1]},
    }
    s.update(kw)
    return s


D = "AXWindow:Crear tabla dinámica"


def dialog():
    x, y = 556, 300
    return [
        el("dlg", "AXWindow", "Crear tabla dinámica", x, y, 400, 330),
        el("dlg-t1", "AXStaticText", "Elija los datos que desea analizar", x + 20, y + 40, 300, 18, path=[D]),
        el("dlg-r1", "AXRadioButton", "Seleccione una tabla o rango", x + 20, y + 64, 240, 20, value="1", path=[D]),
        el("dlg-range", "AXTextField", "Tabla o rango", x + 40, y + 90, 300, 22, value="Hoja1!$A$1:$D$21", path=[D]),
        el(
            "dlg-r2", "AXRadioButton", "Usar una fuente de datos externa", x + 20, y + 120, 260, 20, value="0", path=[D]
        ),
        el(
            "dlg-t2",
            "AXStaticText",
            "Elija dónde desea colocar el informe de tabla dinámica",
            x + 20,
            y + 160,
            360,
            18,
            path=[D],
        ),
        el("dlg-new", "AXRadioButton", "Nueva hoja de cálculo", x + 20, y + 186, 200, 20, value="1", path=[D]),
        el("dlg-old", "AXRadioButton", "Hoja de cálculo existente", x + 20, y + 210, 200, 20, value="0", path=[D]),
        el("dlg-cancel", "AXButton", "Cancelar", x + 210, y + 290, 80, 26, path=[D]),
        el("dlg-ok", "AXButton", "Aceptar", x + 300, y + 290, 80, 26, path=[D]),
    ]


P = "AXGroup:Campos de tabla dinámica"


def pane(checked=()):
    x = 1186
    out = [
        el("pane", "AXGroup", "Campos de tabla dinámica", x, 210, 320, 690, path=[W]),
        el("pane-search", "AXTextField", "Buscar", x + 10, 240, 300, 22, value="", path=[W, P]),
    ]
    for i, f in enumerate(["Región", "Producto", "Fecha", "Ventas"]):
        out.append(
            el(
                f"fld-{f}",
                "AXCheckBox",
                f,
                x + 14,
                272 + 22 * i,
                200,
                20,
                value="1" if f in checked else "0",
                path=[W, P],
            )
        )
    for i, a in enumerate(["Filtros", "Columnas", "Filas", "Valores"]):
        out.append(el(f"area-{a}", "AXList", a, x + 10 + 150 * (i % 2), 420 + 200 * (i // 2), 145, 180, path=[W, P]))
    if "Región" in checked:
        out.append(el("in-rows", "AXStaticText", "Región", x + 165, 640, 120, 18, path=[W, P, "AXList:Filas"]))
    if "Ventas" in checked:
        out.append(
            el("in-values", "AXStaticText", "Suma de Ventas", x + 165, 840, 120, 18, path=[W, P, "AXList:Valores"])
        )
    return out


def pivot_sheet():
    return sheet("Hoja2", 20, 4, lambda r, c: "")


snapshots = {
    "start": snap(chrome("Inicio") + sheet("Hoja1", 40, 6, data), 0),
    "insertar": snap(chrome("Insertar") + sheet("Hoja1", 40, 6, data), 3000, pointer=(94, 84)),
    "dialog": snap(
        chrome("Insertar") + sheet("Hoja1", 40, 6, data) + dialog(),
        6500,
        window_title="Crear tabla dinámica",
        pointer=(40, 120),
    ),
    "pane": snap(chrome("Analizar tabla dinámica", PIVOT_TABS) + pivot_sheet() + pane(), 11000, pointer=(896, 603)),
    "region": snap(
        chrome("Analizar tabla dinámica", PIVOT_TABS) + pivot_sheet() + pane(["Región"]), 15000, pointer=(1300, 283)
    ),
    "ventas": snap(
        chrome("Analizar tabla dinámica", PIVOT_TABS) + pivot_sheet() + pane(["Región", "Ventas"]),
        19000,
        pointer=(1300, 349),
    ),
    "finder": snap(
        [
            el("fwin", "AXWindow", "Descargas", 200, 100, 900, 600),
            el("f-side", "AXOutline", "Barra lateral", 200, 140, 180, 560, path=["AXWindow:Descargas"]),
            el(
                "f-dl",
                "AXStaticText",
                "Descargas",
                210,
                160,
                120,
                18,
                path=["AXWindow:Descargas", "AXOutline:Barra lateral"],
            ),
        ],
        7000,
        window_title="Descargas",
        app="Finder",
    ),
    "secure": snap([], 8000, secure_field_focused=True),
    "format_dialog": snap(
        chrome("Insertar")
        + sheet("Hoja1", 40, 6, data)
        + [
            el("fmt", "AXWindow", "Formato de celdas", 500, 260, 480, 420),
            el("fmt-ok", "AXButton", "Aceptar", 880, 640, 80, 26, path=["AXWindow:Formato de celdas"]),
        ],
        7000,
    ),
}


def step(instruction, speak, target, done_when, hints):
    return {
        "type": "step",
        "instruction": instruction,
        "speak": speak,
        "target": target,
        "done_when": done_when,
        "hint_levels": hints,
    }


plan = [
    step(
        "Haz clic en la pestaña Insertar de la cinta.",
        "Primero, haz clic en Insertar, arriba a la izquierda.",
        {
            "title_regex": "^Insertar$|^Insert$",
            "role": "AXRadioButton",
            "ax_path": ["AXTabGroup"],
            "description": "la pestaña Insertar de la cinta, arriba a la izquierda",
        },
        {"kind": "ax_exists", "args": {"role": "AXButton", "title_regex": "^Tabla dinámica$|^PivotTable$"}},
        [
            "Está en la cinta, arriba, la segunda pestaña.",
            "Las tablas dinámicas se crean desde Insertar.",
            "Insertar muestra los botones para añadir tablas, gráficos e imágenes.",
        ],
    ),
    step(
        "Haz clic en Tabla dinámica, a la izquierda de la cinta.",
        "Ahora pulsa Tabla dinámica, a la izquierda.",
        {
            "title_regex": "^Tabla dinámica$|^PivotTable$",
            "role": "AXButton",
            "description": "el botón Tabla dinámica a la izquierda de la cinta Insertar",
        },
        {
            "kind": "ax_exists",
            "args": {
                "role": "AXWindow",
                "title_regex": "Crear tabla dinámica|Tabla dinámica desde tabla o rango|Create PivotTable",
            },
        },
        [
            "Es el primer botón de la cinta Insertar.",
            "Abre el cuadro para elegir los datos.",
            "Crea una tabla que resume tus datos.",
        ],
    ),
    step(
        "Comprueba el rango y haz clic en Aceptar.",
        "Deja Nueva hoja de cálculo y pulsa Aceptar.",
        {
            "title_regex": "^Aceptar$|^OK$",
            "role": "AXButton",
            "ax_path": ["AXWindow:Crear tabla dinámica"],
            "description": "el botón Aceptar abajo a la derecha del cuadro",
        },
        {"kind": "ax_exists", "args": {"title_regex": "Campos de tabla dinámica|PivotTable Fields"}},
        [
            "Abajo a la derecha del cuadro.",
            "Así Excel crea la tabla en una hoja nueva.",
            "Aceptar confirma el rango y el destino.",
        ],
    ),
    step(
        "Marca Región en Campos de tabla dinámica.",
        "Marca la casilla Región, en el panel de la derecha.",
        {
            "title_regex": "^Región$|^Region$",
            "role": "AXCheckBox",
            "description": "la casilla Región en el panel Campos de tabla dinámica",
        },
        {"kind": "ax_value", "args": {"title_regex": "^Región$|^Region$", "value_regex": "^1$"}},
        [
            "En el panel de la derecha.",
            "Región va a Filas: una fila por región.",
            "Cada campo marcado se añade a la tabla.",
        ],
    ),
    step(
        "Marca Ventas para sumarlas por región.",
        "Y ahora marca Ventas para sumarlas.",
        {"title_regex": "^Ventas$|^Sales$", "role": "AXCheckBox", "description": "la casilla Ventas en el panel"},
        {"kind": "ax_value", "args": {"title_regex": "^Ventas$|^Sales$", "value_regex": "^1$"}},
        [
            "Debajo de Región, en el mismo panel.",
            "Ventas va a Valores y se suma.",
            "Los números van a Valores como Suma de Ventas.",
        ],
    ),
]

fixture = {
    "name": "excel-pivot-es",
    "synthetic": True,
    "note": "Synthetic Excel for Mac (Spanish UI) pivot-table walkthrough: AX snapshots before/after each step, the "
    "scripted plan a tutor would return (from modes/apps/excel.md) and the recorded completion moments. "
    "Not recorded on a real Mac; replace with ink-tutor/fixtures/screens/ once the screen recorder exists.",
    "hello": {
        "type": "screen.hello",
        "helper": "fixture",
        "displays": [{"id": 1, "frame": {"x": 0, "y": 0, "w": 1512, "h": 982}, "scale": 2, "main": True}],
    },
    "goal": "Haz una tabla dinámica de ventas por región",
    "plan": plan,
    "snapshots": snapshots,
    "replay": [
        {"snapshot": "start", "t_ms": 0},
        {"snapshot": "insertar", "t_ms": 3000, "completes": 1},
        {"snapshot": "dialog", "t_ms": 6500, "completes": 2},
        {"snapshot": "pane", "t_ms": 11000, "completes": 3},
        {"snapshot": "region", "t_ms": 15000, "completes": 4},
        {"snapshot": "ventas", "t_ms": 19000, "completes": 5},
    ],
}


def dump(v, ind=0):
    pad = " " * ind
    if (
        (isinstance(v, dict) and "role" in v and "frame" in v)
        or (isinstance(v, dict) and v.get("type") == "step")
        or not isinstance(v, (dict, list))
    ):
        return json.dumps(v, ensure_ascii=False, separators=(", ", ": "))
    if isinstance(v, list):
        if not v or all(not isinstance(x, (dict, list)) for x in v):
            return json.dumps(v, ensure_ascii=False)
        return "[\n" + ",\n".join(pad + " " + dump(x, ind + 1) for x in v) + "\n" + pad + "]"
    return "{\n" + ",\n".join(f"{pad} {json.dumps(k)}: {dump(x, ind + 1)}" for k, x in v.items()) + "\n" + pad + "}"


OUT.write_text(dump(fixture) + "\n")
print({k: len(v["elements"]) for k, v in snapshots.items()})
