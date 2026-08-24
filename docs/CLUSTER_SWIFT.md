# Ejecución en el clúster `swift`

## Infraestructura observada (2026-08-24)

- Sistema: NixOS 25.11.
- Scheduler: Slurm 25.05.
- Cuenta Slurm: `gulliver`.
- GPU disponibles: NVIDIA L4, L40S, RTX A6000 y Tesla V100.
- El nodo de acceso no expone GPU ni Python global; los cálculos deben enviarse
  a Slurm y el entorno se crea con Nix.

La configuración puede cambiar. Comprueba el estado antes de lanzar una campaña:

```bash
sinfo -o "%P|%a|%l|%D|%G|%m|%C"
squeue -u "$USER"
```

## Despliegue

Ruta propuesta:

```text
/home/erosas/projects/opticalflow-activenematics
```

No guardes secuencias grandes dentro de Git. Colócalas en `data/raw/` o en el
almacenamiento de datos que se acuerde para la campaña. `data/`, `results/` y
`logs/` están ignorados, salvo archivos marcadores/documentación explícitos.

## Prueba de referencia

Desde la raíz del proyecto:

```bash
bash cluster/submit_example.sh
```

El comando devuelve un `JOB_ID`. Observación:

```bash
squeue -j <JOB_ID>
sacct -j <JOB_ID> --format=JobID,State,Elapsed,MaxRSS,ExitCode
tail -f logs/example_<JOB_ID>.out
```

Resultados esperados:

```text
results/example/flow.npz
results/example/metadata.json
results/example/velocity_overlay.png
```

## Uso interactivo corto

Para diagnóstico, no para campañas largas:

```bash
srun --partition=oldcpu \
  --gres=gpu:nvidia_rtx_a6000:1 \
  --time=00:15:00 --mem=16G --cpus-per-task=2 \
  --pty bash

cd ~/projects/opticalflow-activenematics
nix-shell shell.nix --run "bash cluster/setup_env.sh"
.venv/bin/python scripts/check_environment.py
```

## Transferencia de imágenes

Desde PowerShell en Windows:

```powershell
scp "C:\ruta\a\mis_imagenes\*.tif" `
  swift:/home/erosas/projects/opticalflow-activenematics/data/raw/
```

Para colecciones grandes es preferible `rsync` desde un sistema que lo tenga
disponible o el almacenamiento compartido del laboratorio. Conserva siempre los
datos brutos como solo lectura y escribe productos derivados en `results/`.

## Campaña por lotes con cuota limitada

Cuando las imágenes solo son accesibles desde Windows, ejecuta desde la raíz del
repositorio:

```powershell
.\scripts\run_staged_full_sequence.ps1 -PlanOnly
.\scripts\run_staged_full_sequence.ps1
```

El controlador construye lotes con un máximo de 5 GiB y un frame solapado para
no perder el par situado en la frontera. Para cada lote:

1. copia los frames desde `ACTNEM` a un staging local ignorado por Git;
2. los transfiere a una ruta temporal y acotada de `swift`;
3. envía `cluster/run_sequence_batch.slurm` y espera su estado final;
4. descarga los campos, abre todos los NPZ y verifica rango, forma, tipo y
   valores finitos;
5. solo después de esa verificación elimina del clúster la entrada temporal y
   la salida ya recuperada.

La ejecución es reanudable: un lote local válido se vuelve a verificar y se
omite. Ante un job fallido o una descarga corrupta, los artefactos remotos se
conservan para diagnóstico. `-KeepRemoteArtifacts` desactiva toda limpieza
remota intencionadamente.

La configuración completa usa `float16` para almacenamiento, manteniendo la
inferencia y las estadísticas en `float32`. En los 12 pares piloto, la
cuantización tuvo un error absoluto máximo de 0.0078 px/frame. El campo denso
completo se estima en aproximadamente 41–44 GiB; `float32` ocuparía unos 87 GiB
y no cabe actualmente en el disco local disponible.

## Reproducibilidad

Cada corrida debe conservar:

- commit de Git (`git rev-parse HEAD`);
- job de Slurm y nodo;
- `metadata.json` de cada análisis;
- calibración espacial y temporal;
- nombres originales y orden de frames;
- cualquier preprocesamiento aplicado antes de RAFT.

## Entorno de software

`shell.nix` se limita a proporcionar Python 3.11 y `virtualenv`. Las librerías de
análisis se fijan en `requirements-cluster.txt`, incluyendo el wheel CUDA 12.1
de PyTorch 2.2.1. El entorno persistente vive en `.venv/` y no se versiona.

Para reconstruirlo desde cero, mueve el entorno anterior fuera del proyecto o
elimínalo conscientemente y vuelve a ejecutar `cluster/setup_env.sh` dentro de
`nix-shell`. No borres `.venv` mientras haya jobs que lo estén usando.
