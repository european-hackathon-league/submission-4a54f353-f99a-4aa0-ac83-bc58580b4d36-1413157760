# Car turntable sheets

The Vehicle plan shows each car turning slowly in a small card. That card is not live 3D: it plays a
pre-rendered sheet (`web/public/models/*-turn.webp`, 60 pictures, 10 across and 6 down) with a CSS
animation, so the page runs no WebGL and no script for it. The real 3D view loads only on click.

Make or refresh a sheet after a model changes:

```
DEPS_DIR=<folder whose node_modules has puppeteer-core and sharp> \
THREE_DIR=<path to node_modules/three> \
node render-turntable.mjs <model.glb> <out.webp> <front> <length> 60 10 <start>
```

| car | model | front | length | start |
|---|---|---|---|---|
| 5 Series | `web/public/models/g60-i5.glb` | `-x` | 5.06 | 0 |
| 7 Series | `web/public/models/g70-i7.glb` | `-z` | 5.39 | 180 |

`front` says which way the car's nose points in the file, `start` turns the first picture (degrees).
Each picture is 640 x 240 px with a transparent background and a soft shadow, lit like the app's 3D view.
Keep 60 pictures in 10 columns: the CSS (`car-sheet-x`, `car-sheet-y` in `web/src/app/globals.css`) expects it.
