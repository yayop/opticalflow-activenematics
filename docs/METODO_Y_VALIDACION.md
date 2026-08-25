# Método y plan de validación

## Qué hace RAFT

RAFT extrae características de dos imágenes, construye un volumen de correlación
entre ellas y refina iterativamente un campo denso de desplazamientos. Cada píxel
recibe dos componentes:

- `vx`: desplazamiento horizontal, positivo hacia la derecha;
- `vy`: desplazamiento vertical, positivo hacia abajo en coordenadas de imagen.

El modelo del repositorio upstream fue aplicado a microscopía de nemáticos
activos y comparado con PIV. Eso no elimina la necesidad de validarlo con la
óptica, marcado, exposición y cadencia de nuestro experimento.

## Pipeline inicial

```text
frames brutos → control de calidad → pareja t,t+Δt → RAFT
             → flujo px/frame → calibración µm/s → visualización + métricas
```

Los archivos `flow.npz` contienen el campo completo. La imagen de flechas es una
visualización submuestreada y no debe usarse como dato cuantitativo.

`grid-step` solo controla el espaciado de las flechas de la figura. No cambia la
resolución del campo RAFT guardado, que sigue teniendo un vector por píxel, ni
añade observaciones independientes a una correlación. Para estudiar una malla
de comparación de 12 px hay que muestrear explícitamente los campos sobre esa
malla y considerar su autocorrelación espacial.

## Vorticidad sobre la malla de 12 px

Para un flujo `u=dx/dframe`, `v=dy/dframe` en coordenadas de imagen (`+x` a la
derecha, `+y` hacia abajo), se guarda

```text
omega_image = dv/dx - du/dy
```

Las derivadas usan el espaciado físico de la malla, `dx=dy=12 px`, no un paso
unitario entre índices. Por ello `omega_image` tiene unidades `1/frame`. Con la
cadencia registrada de `delta_t=0.5 s`, `omega_image / 0.5` tiene unidades
`1/s`. Si se transforma a coordenadas cartesianas con `+y` hacia arriba, la
vorticidad cambia de signo: `omega_cartesian = -omega_image`.

Se usan diferencias centradas de segundo orden en el interior y diferencias
unilaterales de segundo orden en el borde (`numpy.gradient`, `edge_order=2`).
El producto canónico se guarda en `float32` bajo la clave
`vorticity_image_per_frame`; la conversión temporal se conserva en los
metadatos y en el resumen, evitando duplicar el mismo campo escalado.

Cuando haya una inclusión, la máscara puede excluirla de estadísticas y
visualizaciones. Además debe excluirse o dilatarse una franja alrededor de su
borde: una derivada que cruza desde fluido válido hacia una región enmascarada
no representa la vorticidad del fluido. La campaña actual calcula primero el
campo geométrico completo y no aplica una máscara implícita.

## Validación mínima antes de analizar una campaña

1. **Reproducibilidad upstream.** Ejecutar los dos frames incluidos y comparar
   el resultado con `example/velocity_plot.png`.
2. **Escala de entrada.** Comparar `--input-scale upstream` y `raft`. Mantener la
   variante publicada como referencia hasta tener una métrica independiente.
3. **Desplazamiento sintético.** Trasladar una imagen una cantidad conocida y
   medir sesgo, error absoluto y dispersión lejos de los bordes.
4. **Control inmóvil.** Analizar frames repetidos o una muestra pasiva para
   estimar el suelo de ruido.
5. **Deriva global.** Medir si hay traslación de cámara/muestra y decidir si se
   resta antes de interpretar flujos internos.
6. **Comparación física.** Usar trazadores, tracking o PIV en un subconjunto.
7. **Sensibilidad temporal.** Repetir con separaciones de 1, 2, ... frames para
   localizar el rango donde el movimiento es resoluble sin perder asociación.
8. **Malla estadística.** Comparar espaciados de 12, 16, 24 y 48 px, cuantificar
   estabilidad de correlaciones y estimar el número efectivo de observaciones
   considerando la longitud de correlación espacial. La malla de 12 px queda
   registrada como validación pendiente; no debe confundirse con `grid-step`.

## Metadatos experimentales necesarios

- tamaño de píxel efectivo en µm/px;
- tiempo real entre frames en segundos;
- aumento, objetivo y cámara;
- canal, exposición, binning y bit depth;
- fecha, muestra y condición experimental;
- preprocesamiento (flat field, background, denoise, registro, recorte);
- orientación física de los ejes de la imagen.

## Riesgos conocidos

- La conservación de intensidad puede fallar con fotoblanqueo o fluctuaciones
  del marcador.
- El flujo óptico sigue textura/intensidad y no necesariamente filamentos
  individuales.
- Bordes, regiones saturadas y bajo contraste suelen producir estimaciones menos
  fiables.
- El volumen de correlación estándar de RAFT crece rápidamente con el área de la
  imagen; hay que registrar cualquier estrategia de teselado y solapamiento.
- Convertir a µm/s no corrige por sí solo deriva, perspectiva o deformaciones.
