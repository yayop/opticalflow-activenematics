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
