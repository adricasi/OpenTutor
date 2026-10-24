# Encargo: prototipo navegable de OpenTutor

> Documento preparado para darse a Claude (Claude Code u otro agente) como encargo de trabajo.
> Rama de trabajo: **`feat/prototype`** (ya creada a partir de `main`; no tocar `main` ni el código existente).

## 1. Objetivo

Construir un **prototipo navegable de las pantallas de OpenTutor**, priorizando que sea
**muy fácil de usar** (user friendly): pantallas grandes, texto claro y simple, pocas
decisiones por pantalla, botones grandes pensados para móviles baratos.

Es un prototipo de interfaz: **sin backend, sin modelo y sin llamadas de red reales.**
Las respuestas del tutor se simulan (datos preparados y streaming simulado). El resto
del repositorio **no se toca**.

## 2. Contexto del producto (resumen de README.md y docs/spec/)

OpenTutor convierte un portátil Linux con **Gemma 4** (vía Ollama, en local) en un
**tutor de deberes gratuito y privado** para todo un grupo:

- Los alumnos conectan el móvil a la **Wi-Fi del portátil** (sin internet, sin datos
  móviles) y escanean un **código QR**.
- Entran **solo con un apodo**; sin cuentas, sin datos personales.
- Preguntan **por foto** (hoja de ejercicios, el modelo la lee) o **escritas**.
- Materias al lanzamiento: **Maths, English language, Science** (tutores configurables
  por carpeta, no por código).
- **Pista antes que solución** (hint-first): escalerilla de 4 niveles — nudge → hint →
  siguiente paso → solución completa solo si el alumno la pide.
- **Comprobación de intentos**: veredicto estructurado (correct / partly_correct /
  not_yet_correct); en Maths decide primero un comprobador simbólico.
- **Práctica**: "give me 3 similar exercises" con comprobación.
- **Cola justa**: si el modelo está ocupado, el alumno ve su posición en la fila.
- **Vista del profesor** con PIN: temas preguntados y puntos de dificultad agregados,
  sin datos personales.
- **Privacidad total**: nada se guarda en disco; todo se borra al pulsar **Finish**,
  tras 30 min de inactividad o al reiniciar el hub.
- Público objetivo: **secundaria, familias sin internet ni datos**, móviles baratos.
  Interfaz en inglés (esta versión), lenguaje sencillo.

## 3. Pantallas a prototipar

1. **Join** — QR ilustrado + campo "Nickname" (reglas visibles: letras/números,
   máx. 20, único) + botón grande "Start". Errores: apodo en uso, hub lleno.
2. **Subjects** — tres tarjetas grandes (Maths, English, Science) con icono y
   descripción de una línea; tap para elegir.
3. **Chat** (por materia) — hilo de conversación; composer con **foto** (cámara o
   galería) y **texto**; acciones rápidas fijas y bien visibles:
   - "Another hint" · "Show the answer" · "Check my answer" · "Practice" · "New exercise"
   - Estados: esperando (con streaming simulado), **posición en la cola** ("You are
     #2 in line"), veredicto de comprobación (✔ correct / ~ partly / ✘ not yet),
   - Botón **Finish** destacado con confirmación ("Everything will be deleted. This
     cannot be undone.") y pantalla de despedida.
4. **Teacher insights** (PIN) — pantalla simple: materias, temas más preguntados,
   dónde se atascó la gente, sin datos personales. Login por PIN de 6 dígitos.
5. Opcional: **pantalla de inicio del hub** (QR + dirección + PIN impreso) para demo.

## 4. Criterios de aceptación

- Navegable de punta a punta con datos simulados: Join → Subject → Chat (foto o texto,
  pista, respuesta, check, práctica) → Finish, y Teacher insights con PIN.
- Probable a 360 px de ancho (móvil barato); botones y texto grandes; nada crítico
  depende de precisión táctil pequeña.
- Cero llamadas de red (solo archivos locales); respuestas simuladas con retardo y
  "streaming" para demostrar la sensación del producto real.
- Sin cuentas, sin cookies persistentes, sin datos personales inventados realistas.
- Todo en la rama `feat/prototype`, bajo una carpeta nueva (p. ej. `prototype/`),
  sin modificar código existente.

## 5. Fuentes dentro del repo (léelas antes de empezar)

- `README.md` — producto y guía de operación.
- `docs/spec/product.md` — problema, usuarios, objetivos, métricas.
- `docs/spec/design.md` — pantallas, eventos de streaming, escalera de pistas,
  comprobador de matemáticas, seguridad (cap. "Data model", "Interfaces", "Components").
- `docs/spec/requirements.md` — requisitos REQ-* y criterios de aceptación AC-*.

## 6. Notas de estilo

- Producto serio y cálido, no infantilizado; copy corto y en inglés sencillo
  (la versión final está en inglés; comentarios del código en inglés también).
- Simula el streaming token a token para que la demo "se sienta" en vivo.
- El tono del tutor: anima, nunca da la respuesta de primero, recuerda "check with
  your teacher" cuando toque.
