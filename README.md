# Optical flow para active nematics

Proyecto reproducible para estimar campos densos de desplazamiento y velocidad en
secuencias de microscopía de nemáticos activos. Esta adaptación parte de
[`tranngocphu/opticalflow-activenematics`](https://github.com/tranngocphu/opticalflow-activenematics),
que aplica una arquitectura RAFT entrenada para este tipo de imágenes.

> Estado: infraestructura validada en `swift` con el ejemplo de referencia y
> con un piloto real de 12 pares de `Bulk_1_12_11`. Consulta la
> [bitácora](docs/BITACORA.md).

## Qué produce

Para dos frames consecutivos, `scripts/analyze_pair.py` genera:

- `flow.npz`: campo denso `(H, W, 2)`, componentes y módulo;
- `velocity_overlay.png`: vectores superpuestos al primer frame;
- `metadata.json`: parámetros, versiones, GPU y estadísticas básicas.

RAFT devuelve desplazamiento en **píxeles por frame**. Si se proporcionan el
tamaño de píxel y el intervalo temporal, el script añade velocidad en µm/s:

```text
velocidad [µm/s] = flujo [px/frame] × tamaño_de_píxel [µm/px] / Δt [s/frame]
```

## Ejecución recomendada: clúster `swift`

El proyecto se edita/documenta en Windows, pero la inferencia se ejecuta en
Linux con GPU mediante Slurm y Nix.

```bash
ssh swift
cd ~/projects/opticalflow-activenematics
bash cluster/submit_example.sh
squeue -u "$USER"
```

Cuando termine:

```bash
cat logs/example_<JOB_ID>.out
cat logs/example_<JOB_ID>.err
ls -lh results/example
```

El job de ejemplo solicita la RTX A6000 de la partición `oldcpu`, 32 GB de RAM y
30 minutos. Nix proporciona Python y `virtualenv`; `cluster/setup_env.sh` crea
`.venv` e instala las versiones fijadas en `requirements-cluster.txt`. La primera
ejecución tarda mientras descarga el wheel CUDA de PyTorch; las siguientes
reutilizan el entorno.

Para una pareja propia:

```bash
nix-shell shell.nix --run "bash cluster/setup_env.sh"
.venv/bin/python scripts/analyze_pair.py \
  data/raw/frame_000.tif data/raw/frame_001.tif \
  --output-dir results/experimento_01/pair_000_001 \
  --pixel-size-um 0.108 \
  --delta-t-s 2.0 \
  --device cuda
```

Sustituye `0.108` y `2.0` por la calibración real del microscopio y la adquisición.

Para una secuencia numerada, el modelo se carga una sola vez y se reutiliza:

```bash
.venv/bin/python scripts/analyze_sequence.py \
  data/Bulk_1_12_11/raw_pilot_frames \
  --output-dir results/Bulk_1_12_11/raw_pilot \
  --pairs 1-12 \
  --delta-t-s 0.5 \
  --device cuda
```

El resultado incluye un NPZ y un overlay por par, además de `summary.csv` y
`metadata.json`. `scripts/compare_raft_pivlab.py` permite después construir
paneles RAFT/PIVlab y calcular correlaciones sobre la malla de PIVlab.
`scripts/compare_preprocessing.py` compara campos obtenidos con dos
preprocesamientos y localiza sus diferencias vectoriales.

Para la secuencia completa, el controlador de Windows divide la transferencia
en lotes reanudables de hasta 1 GiB, envía un job por lote y solo limpia las
copias de `swift` después de verificar la descarga:

```powershell
.\scripts\run_staged_full_sequence.ps1 -PlanOnly
.\scripts\run_staged_full_sequence.ps1
```

La primera campaña completa guarda los campos densos en `float16` y no genera 7199
overlays. Las imágenes de inspección pueden producirse posteriormente para una
selección temporal sin repetir la inferencia.

Para campañas múltiples se usa una malla cuantitativa de 12 px. `swift` copia
la entrada directamente desde `ACTNEM`, ejecuta RAFT a resolución completa,
guarda `flow[::12, ::12]` y sincroniza los resultados verificados al NAS:

```powershell
.\scripts\run_all_bulk_grid12.ps1 -PlanOnly
.\scripts\run_all_bulk_grid12.ps1
```

Cada raíz recibe una carpeta hermana de `ImageSequence` llamada
`OpticalFlow_RAFT_grid12`. La ejecución es reanudable por lotes y Windows no
almacena las nuevas campañas.

## Estructura

```text
cluster/                 Jobs y envío a Slurm
docs/                    Método, guía del clúster y bitácora
example/                 Dos frames y resultado originales de referencia
models/weights.pth       Pesos publicados por el proyecto original
RAFT/                    Implementación RAFT incluida por el proyecto original
scripts/                 Análisis por pareja/secuencia y comparación con PIVlab
shell.nix                Entorno Linux/Nix con PyTorch CUDA
data/                    Datos propios locales (ignorados por Git)
results/                 Resultados generados (ignorados por Git)
logs/                    Salidas de Slurm (ignoradas por Git)
```

## Decisiones que deben validarse científicamente

1. `--input-scale upstream` reproduce el preprocesamiento publicado: el tensor
   entra en el intervalo 0–1 aunque la implementación RAFT vuelve a dividir por
   255. Es una convención inusual y debe compararse contra `--input-scale raft`
   antes de cambiarla.
2. El flujo es movimiento aparente de intensidad, no una medida física de
   velocidad hasta aplicar la calibración espacial y temporal.
3. La dirección vertical sigue coordenadas de imagen: `+y` apunta hacia abajo.
4. Deben evaluarse sensibilidad a `--iterations`, densidad de marcado,
   fotoblanqueo, deriva global, ruido y distancia temporal entre frames.
5. Imágenes grandes pueden requerir recorte o inferencia por teselas debido al
   coste cuadrático del volumen de correlación de RAFT.

Más detalle en [Método y validación](docs/METODO_Y_VALIDACION.md) y
[Uso de `swift`](docs/CLUSTER_SWIFT.md).

## Procedencia, citas y licencia

La base de código, los pesos y los datos de ejemplo proceden del repositorio de
Phu N. Tran y colaboradores. Si utilizas este trabajo, cita tanto su artículo
([arXiv:2404.15497](https://arxiv.org/abs/2404.15497)) como RAFT
([arXiv:2003.12039](https://arxiv.org/abs/2003.12039)).

El repositorio upstream no publica un archivo de licencia. Por tanto, no se
presupone permiso para relicenciar o redistribuir su código y pesos fuera de los
términos aplicables de GitHub. Esta adaptación debe publicarse como fork con
atribución clara, o separando el código propio del upstream, hasta que los
autores aclaren la licencia.
