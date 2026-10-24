# Microsoft Excel (macOS) — recipe

Matched when the frontmost app's name or bundle id contains "excel". The Layout part always goes to the planner;
a task section (one with a `keywords:` line) only when the user's goal mentions one of its keywords, so a recipe
never steers an unrelated request. The reference Excel is in **Spanish**; English
titles are given after the slash. Use whichever the element list shows (title_regex can carry both).

## Layout
- Ribbon tabs are `AXRadioButton`s in an `AXTabGroup` at the top of the window: Inicio / Home, Insertar / Insert,
  Dibujar / Draw, Disposición de página / Page Layout, Fórmulas / Formulas, Datos / Data, Revisar / Review,
  Vista / View. The selected tab has value "1".
- The ribbon's buttons are `AXButton`s (some `AXMenuButton`s) in an `AXGroup` below the tabs; they change with the
  selected tab. A button of a tab exists only while that tab is selected, which makes it a good `done_when` for
  "click the tab".
- Sheet cells are NOT in the element list (the grid is one `AXLayoutArea`), so a cell can only be found in the
  screenshot, which is slow. Sheet tabs at the bottom are `AXRadioButton`s (Hoja1 / Sheet1).
- Dialogs are `AXWindow`s titled with the dialog's name; their buttons are Aceptar / OK and Cancelar / Cancel.

## Pivot table (tabla dinámica) from a table of data
keywords: tabla dinámica, tablas dinámicas, pivot, dinámica, resumen por
1. (Usually omit.) A file opens with A1 selected, which is inside the data: Insertar → Tabla dinámica then takes the
   whole table. Only add "click a cell of the data" if the user said they selected something outside it; then
   done_when: user_next.
2. Click the **Insertar / Insert** tab.
   target: {"title_regex":"^Insertar$|^Insert$","role":"AXRadioButton","ax_path":["AXTabGroup"],
   "description":"the Insertar tab in the ribbon, top left"}
   done_when: {"kind":"ax_exists","args":{"role":"AXButton","title_regex":"^Tabla dinámica$|^PivotTable$"}}
3. Click **Tabla dinámica / PivotTable** (left end of the Insertar ribbon).
   target: {"title_regex":"^Tabla dinámica$|^PivotTable$","role":"AXButton","description":"the Tabla dinámica button at the left of the Insertar ribbon"}
   done_when: {"kind":"ax_exists","args":{"role":"AXWindow","title_regex":"Crear tabla dinámica|Tabla dinámica desde tabla o rango|Create PivotTable|PivotTable from table or range"}}
4. In the dialog **Crear tabla dinámica** (newer builds: **Tabla dinámica desde tabla o rango**; English:
   Create PivotTable / PivotTable from table or range), check that "Tabla o rango" / "Table/Range" holds the data
   and that **Nueva hoja de cálculo / New Worksheet** is chosen, then click **Aceptar / OK**.
   target: {"title_regex":"^Aceptar$|^OK$","role":"AXButton","ax_path":["AXWindow:Crear tabla dinámica"],"description":"the Aceptar button at the bottom right of the dialog"}
   done_when: {"kind":"ax_exists","args":{"title_regex":"Campos de tabla dinámica|PivotTable Fields"}}
5. In the pane **Campos de tabla dinámica / PivotTable Fields** (right side), tick the field to group by
   (e.g. **Región / Region**): it goes to **Filas / Rows**.
   target: {"title_regex":"^Región$|^Region$","role":"AXCheckBox","description":"the Región checkbox in the Campos de tabla dinámica pane"}
   done_when: {"kind":"ax_value","args":{"title_regex":"^Región$|^Region$","value_regex":"^1$"}}
6. Tick the field to add up (e.g. **Ventas / Sales**): it goes to **Valores / Values** as "Suma de Ventas".
   target: {"title_regex":"^Ventas$|^Sales$","role":"AXCheckBox","description":"the Ventas checkbox in the pane"}
   done_when: {"kind":"ax_value","args":{"title_regex":"^Ventas$|^Sales$","value_regex":"^1$"}}

Other names in the pane: Filtros / Filters, Columnas / Columns, Filas / Rows, Valores / Values. Field names are the
data's own column headers: use the ones in the element list.
