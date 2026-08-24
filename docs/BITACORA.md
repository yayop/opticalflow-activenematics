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

## 2026-08-24 — Piloto sobre `Bulk_1_12_11`

### Objetivo y datos

- Se procesaron los pares 1→2 hasta 12→13 de la secuencia cruda
  `Bulk_1_12_11/ImageSequence` alojada en `ACTNEM`.
- La secuencia completa contiene 7200 TIFF `uint16` de 1120 × 1578 px y ocupa
  aproximadamente 23.9 GiB.
- Los 13 TIFF usados se copiaron al proyecto y el directorio original se trató
  como solo lectura.
- Se inspeccionaron las referencias existentes `PIVlab/PIVlab.mat` y
  `PIVlab2/PIVlab.mat`; ambas contienen 7199 campos, uno por par consecutivo.

### Implementación

- Se corrigió la carga de TIFF de 16 bits: ahora los enteros se normalizan por
  el máximo de su tipo (`65535` para `uint16`) antes de convertirlos a RGB.
  La conversión anterior mediante Pillow podía saturar la imagen.
- Se añadió `scripts/analyze_sequence.py`, que carga RAFT una sola vez, procesa
  un intervalo de pares y guarda campos NPZ, overlays, CSV y metadatos.
- Se añadió `scripts/compare_raft_pivlab.py` para interpolar RAFT sobre las
  mallas de PIVlab, calcular acuerdo y producir paneles con escala vectorial
  común.

### Corrida GPU

- Commit ejecutado: `9f5730c3300f58f6897deda1ea5f87cdfe8a58ea`.
- Job Slurm `1035138`, `node5`, NVIDIA RTX A6000; estado `COMPLETED`.
- Parámetros: 24 iteraciones RAFT, escala de entrada `upstream`, paso de flechas
  24 px e intervalo temporal provisional de 0.5 s (2 fps).
- Tiempo de pared del job: 36 s. Procesamiento de los 12 pares, incluyendo
  escritura de campos y figuras: 24.25 s.
- Inferencia: 0.661 s para el primer par y 0.358–0.369 s por par después del
  calentamiento. Memoria GPU máxima registrada: 5.89 GiB.
- Rapidez media RAFT: 4.89–5.26 px/frame, equivalente a 9.78–10.53 px/s si el
  intervalo es realmente 0.5 s. Falta la calibración espacial para convertir
  estas cantidades a unidades físicas.

### Inspección y comparación con PIVlab

- Se generaron 12 overlays RAFT y 12 comparaciones de tres paneles sobre el
  mismo fondo: RAFT, PIVlab y PIVlab2.
- La inspección visual muestra campos suaves y coherentes; los remolinos y las
  direcciones principales persisten a lo largo de los 12 pares.
- Frente a `PIVlab`, el promedio de los 12 pares es: correlación de componentes
  `corr(u)=0.938`, `corr(v)=0.945` y coseno direccional medio `0.911`.
- Frente a `PIVlab2`: `corr(u)=0.877`, `corr(v)=0.895` y coseno direccional
  medio `0.867`.
- RAFT estima una magnitud mayor: 5.04 px/frame en promedio, frente a 3.67 para
  PIVlab y 2.80 para PIVlab2. Esto no debe interpretarse todavía como que uno de
  los métodos es correcto: usan estimadores, regularización y escalas espaciales
  diferentes.

### Decisiones y próximo paso

- El piloto confirma que el flujo funciona con los TIFF reales de 16 bits y que
  la geometría obtenida concuerda razonablemente con PIVlab.
- El siguiente control será repetir exactamente los mismos 12 pares y parámetros
  sobre `ImageSequence_divide_background_median`, y comparar crudo contra fondo
  dividido sin cambiar simultáneamente otras variables.

## 2026-08-24 — Control con división por fondo mediano

### Objetivo y datos

- Se repitió el piloto sobre los frames 1–13 de
  `ImageSequence_divide_background_median`, manteniendo los mismos 12 pares y
  parámetros usados para la secuencia cruda.
- Esta secuencia también tiene 7200 frames de 1120 × 1578 px, pero los TIFF son
  `uint8` y usan el patrón `frame_####.tif`.
- Los datos fuente en `ACTNEM` se conservaron sin modificaciones.

### Corrida GPU

- Commit ejecutado: `20b67bec654ed529eaec058187a6747e1dcb0481`.
- Job Slurm `1035140`, `node5`, NVIDIA RTX A6000; estado `COMPLETED`, exit code
  `0:0` y tiempo de pared 38 s.
- Parámetros: 24 iteraciones, escala `upstream`, 0.5 s/frame y paso de flechas
  24 px.
- El procesamiento de los 12 pares tomó 24.98 s; la inferencia estabilizada fue
  0.358–0.369 s/par y la memoria GPU máxima fue 5.89 GiB.

### Comparación crudo vs. fondo dividido

- Rapidez media de campo completo: 5.0327 px/frame para el crudo y 5.0245
  px/frame para el fondo dividido. El cambio medio es −0.00825 px/frame
  (−0.16 %).
- La diferencia vectorial media es 0.2918 px/frame y su percentil 95 medio es
  0.9977 px/frame. El coseno direccional medio es 0.9843.
- Las correlaciones entre los campos son `corr(u)=0.9940` y `corr(v)=0.9960`.
- Al excluir un borde de 64 px por lado, la diferencia vectorial media baja a
  0.2259 px/frame y el coseno direccional sube a 0.9874. Los mapas muestran que
  parte de la diferencia se concentra en los bordes y en estructuras delgadas
  de alto contraste.

### Comparación con PIVlab

- Para el fondo dividido, el acuerdo promedio con PIVlab es
  `corr(u)=0.9375`, `corr(v)=0.9448` y coseno direccional `0.9107`.
- Con PIVlab2 es `corr(u)=0.8771`, `corr(v)=0.8936` y coseno direccional
  `0.8669`.
- Estas cifras son prácticamente iguales a las obtenidas con la secuencia
  cruda. En esta muestra corta, la división por fondo mediano mejora el contraste
  visual, pero no produce una mejora cuantitativa apreciable del acuerdo con
  PIVlab ni cambia la rapidez media.

### Decisión

- El optical flow es robusto frente a este preprocesamiento en los 12 pares
  examinados. No hay evidencia en este piloto para preferir el fondo dividido
  por precisión del campo; sí puede ser útil para inspección visual o para
  regiones afectadas por iluminación no uniforme.
- Antes de decidir el preprocesamiento de la secuencia completa, conviene repetir
  la comparación en varios puntos temporales, especialmente donde cambie la
  iluminación media o el contraste.

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
