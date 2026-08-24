# Bitácora del proyecto

## 2026-08-24 — Inicio y auditoría

### Objetivo

Construir un flujo reproducible para estimar campos de velocidad en imágenes de
microscopía de active nematics a partir de
`tranngocphu/opticalflow-activenematics`, ejecutándolo en el clúster `swift` y
documentándolo en GitHub.

### Trabajo realizado

- Se clonó el repositorio upstream, commit
  `d23cd0869fd1b0cc5efc3853f486917270c35542` (2024-04-26).
- El remoto original se renombró a `upstream` para evitar pushes accidentales.
- Se auditó el entorno local: Windows, Git 2.51.2, Python 3.12 y Quadro P400 de
  2 GB; no se instalará el entorno de cómputo allí.
- Se auditó `swift`: NixOS, Slurm, cuenta `gulliver`, aproximadamente 70 TB
  libres en `/home`, y nodos con L4/L40S/A6000/V100.
- Se detectó que `environment.yaml` contiene builds y librerías específicos de
  Linux y no representa una especificación portátil para Windows.
- Se creó `shell.nix` con Python 3.11/`virtualenv` y un requirements fijado con
  PyTorch 2.2.1 + CUDA 12.1 para reproducir la versión upstream.
- Se añadió una interfaz parametrizable para parejas de imágenes, calibración
  física, resultados NumPy, metadatos JSON y figura de vectores.
- Se creó un job Slurm corto para reproducir el ejemplo en la RTX A6000.

### Hallazgos

- El ejemplo original usa imágenes de 1280 × 1080 px.
- El modelo produce desplazamiento en px/frame; llamarlo “velocidad” requiere
  conocer tamaño de píxel e intervalo temporal.
- El preprocesamiento upstream aplica `ToTensor()` (0–1) antes de una RAFT que
  divide otra vez por 255. Se conserva como opción predeterminada hasta validarlo.
- El repositorio upstream no contiene `LICENSE`; la publicación debe preservar
  procedencia y evitar atribuir una licencia no concedida.
- Se descartó empaquetar PyTorch directamente con Nix porque el canal del
  clúster construía innecesariamente UCX/NVSHMEM/MPI. El wheel CUDA en `.venv`
  mantiene el entorno pequeño y alineado con upstream.
- Se corrigió un fallo upstream en `RAFT/core/raft.py`: comprobar pertenencia
  con `in` sobre `argparse.Namespace` produce `TypeError`; se usa `hasattr`.

### Próximo hito

Completar el job de referencia, registrar tiempo/memoria/resultado y comparar la
salida con la figura incluida. Después, incorporar una pareja real con sus
metadatos de adquisición y diseñar las pruebas sintéticas.

### Validación del ejemplo

- Job final limpio: Slurm `1034473`, nodo `node5`, NVIDIA RTX A6000.
- Estado: `COMPLETED`, exit code `0:0`, tiempo total 12 s.
- Software: Python 3.11.14, PyTorch 2.2.1+cu121, CUDA disponible.
- Entrada: dos TIFF de 1280 × 1080 px; 24 iteraciones; escala `upstream`.
- Salida: campo de 1080 × 1280 × 2, figura, metadatos y NPZ comprimido.
- Estadísticas: rapidez media 10.0834, mediana 8.0300 y máximo 31.5253 px/frame.
- Memoria RAM máxima registrada por Slurm: 822424 KiB. La memoria GPU no fue
  muestreada en esta prueba.
- Inspección visual: el patrón, direcciones y vórtices coinciden con
  `example/velocity_plot.png`; cambia únicamente el formato de la figura.

Los jobs `1034465`, `1034466`, `1034467`, `1034468`, `1034469` y `1034470`
documentan la puesta a punto (ruta de spool de Slurm, dependencia Nix excesiva,
`libstdc++` aislada y bug de `Namespace`). No produjeron resultados científicos.

### Próximo hito actualizado

Incorporar una pareja real con sus metadatos de adquisición y ejecutar controles
sintéticos, de muestra inmóvil y de escala de entrada.

## Plantilla para nuevas entradas

```text
## AAAA-MM-DD — Título

Objetivo:
Datos/commit/job:
Parámetros:
Resultado:
Problemas:
Decisiones:
Próximo paso:
```
