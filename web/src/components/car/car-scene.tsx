"use client";

import { Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { ContactShadows, Environment, Lightformer, OrbitControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { CarModel, Region } from "@/lib/car/zones";

export type SceneMode = "small" | "big";

interface Props {
  model: CarModel;
  /** Region to fly to and light up; null shows the whole car turning. */
  focus: Region | null;
  /** Dark or light studio, to match the app's theme. */
  dark: boolean;
  /** "small" is the slow turning preview; "big" is the explorable view. */
  mode: SceneMode;
  /** True while nobody can see the scene (scrolled away, tab hidden): no frame is drawn. */
  paused: boolean;
  /** Pixels covered by overlays on each side; the view shifts so the car stays in the free space between them. */
  insetLeft?: number;
  insetRight?: number;
  /** The first frame with the car is on screen. */
  onReady?: () => void;
  /** The device cannot keep up even at the lowest quality; the caller should show a still picture instead. */
  onSlow?: () => void;
  /** Car and soft shadow only, for rendering the still pictures. */
  bare?: boolean;
}

const BLAU = new THREE.Color("#0098d4");
const UP = new THREE.Vector3(0, 1, 0);

/**
 * Frames per second while something moves. The scene runs on demand: it draws only when something
 * changes, and while the car turns a timer asks for these few frames, not 60 a second.
 */
const FPS = { small: 20, big: 30 } as const;
/** The highlight breathes slowly; it needs far fewer frames than a camera move. */
const PULSE_FPS = 12;
/** Radians per second. */
const SPIN = { small: 0.26, big: 0.2 } as const;

/** Uniforms shared by every material of the car, so one update moves the highlight on all meshes. */
function makeUniforms() {
  return {
    uZoneCenter: { value: new THREE.Vector3() },
    uZoneRadius: { value: new THREE.Vector3(1, 1, 1) },
    uGlow: { value: 0 },
    uXray: { value: 0 },
    uTime: { value: 0 },
    uColor: { value: BLAU },
  };
}
type Uniforms = ReturnType<typeof makeUniforms>;

/**
 * The models are merged shells, so a part cannot be selected as a mesh.
 * Instead every fragment inside the region's ellipsoid is tinted and glows,
 * and the rest of the body can fade to a see-through shell.
 */
function patchMaterial(material: THREE.MeshStandardMaterial, uniforms: Uniforms) {
  material.side = THREE.DoubleSide;
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vZonePos;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvZonePos = (modelMatrix * vec4(transformed, 1.0)).xyz;");
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        "#include <common>\nvarying vec3 vZonePos;\nuniform vec3 uZoneCenter;\nuniform vec3 uZoneRadius;\nuniform float uGlow;\nuniform float uXray;\nuniform float uTime;\nuniform vec3 uColor;",
      )
      .replace(
        "#include <color_fragment>",
        "#include <color_fragment>\nfloat zoneHit = (1.0 - smoothstep(0.55, 1.0, length((vZonePos - uZoneCenter) / uZoneRadius))) * uGlow;\ndiffuseColor.rgb = mix(diffuseColor.rgb, uColor, zoneHit * 0.85);",
      )
      .replace(
        "#include <normal_fragment_maps>",
        "#include <normal_fragment_maps>\nfloat rim = pow(1.0 - abs(dot(normalize(normal), normalize(vViewPosition))), 2.0);\ndiffuseColor.a = mix(diffuseColor.a, mix(0.05, 0.5, rim), uXray);\ndiffuseColor.a = mix(diffuseColor.a, 0.92, zoneHit);",
      )
      .replace(
        "#include <emissivemap_fragment>",
        "#include <emissivemap_fragment>\ntotalEmissiveRadiance += uColor * zoneHit * (0.75 + 0.25 * sin(uTime * 3.0));",
      );
  };
  material.needsUpdate = true;
}

interface Frame {
  length: number;
  height: number;
  width: number;
}

/** Normalised region coordinates to metres. The car's front points to +X, the roof to +Y, the right side to +Z. */
function regionToWorld(region: Region, f: Frame) {
  const [u, v, w] = region.center;
  const [ru, rv, rw] = region.radius;
  return {
    center: new THREE.Vector3(f.length / 2 - u * f.length, v * f.height, (w * f.width) / 2),
    radius: new THREE.Vector3(ru * f.length, rv * f.height, (rw * f.width) / 2),
  };
}

function cameraFor(region: Region | null, f: Frame, mode: SceneMode) {
  if (!region) {
    const target = new THREE.Vector3(0, f.height * (mode === "small" ? 0.4 : 0.45), 0);
    const distance = f.length * (mode === "small" ? 1.2 : 1.55);
    return { target, position: target.clone().add(new THREE.Vector3(0.62, 0.22, -0.72).normalize().multiplyScalar(distance)) };
  }
  const { center } = regionToWorld(region, f);
  const az = THREE.MathUtils.degToRad(region.view.azimuth);
  const el = THREE.MathUtils.degToRad(region.view.elevation);
  const dir = new THREE.Vector3(Math.cos(az) * Math.cos(el), Math.sin(el), -Math.sin(az) * Math.cos(el));
  return { target: center, position: center.clone().add(dir.multiplyScalar(region.view.distance * f.length)) };
}

/** Body paint gets a clear coat on top of its metallic base, like real car paint. */
function paintFrom(source: THREE.MeshStandardMaterial) {
  const paint = new THREE.MeshPhysicalMaterial();
  THREE.MeshStandardMaterial.prototype.copy.call(paint, source);
  paint.clearcoat = 1;
  paint.clearcoatRoughness = 0.06;
  return paint;
}

/** The file's own axes and size are unknown, so the car is turned, scaled to its real length and put on the ground. */
function buildRig(source: THREE.Object3D, model: CarModel) {
  const uniforms = makeUniforms();
  const scene = source.clone(true);
  // Longest side = length, shortest = height; the model config says which end is the front and which is the roof.
  const raw = new THREE.Box3().setFromObject(scene).getSize(new THREE.Vector3()).toArray();
  const [lengthAxis, , heightAxis] = [0, 1, 2].sort((a, b) => raw[b] - raw[a]);
  const front = new THREE.Vector3().setComponent(lengthAxis, model.flipFront ? -1 : 1);
  const up = new THREE.Vector3().setComponent(heightAxis, model.flipUp ? -1 : 1);
  const right = new THREE.Vector3().crossVectors(front, up);
  const turned = new THREE.Group();
  turned.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(front, up, right).transpose());
  turned.add(scene);
  turned.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(turned);
  const size = box.getSize(new THREE.Vector3());
  const mid = box.getCenter(new THREE.Vector3());
  const scale = model.length / size.x;
  const root = new THREE.Group();
  turned.position.set(-mid.x, -box.min.y, -mid.z);
  root.add(turned);
  root.scale.setScalar(scale);

  const materials: THREE.MeshStandardMaterial[] = [];
  scene.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    // A file without normals would render faceted; smooth ones are computed from its welded mesh.
    if (!mesh.geometry.getAttribute("normal")) {
      mesh.geometry = mesh.geometry.clone();
      mesh.geometry.computeVertexNormals();
    }
    const source = mesh.material as THREE.MeshStandardMaterial;
    const own = source.name === "paint" ? paintFrom(source) : source.clone();
    // Parts that are see-through in the file (headlight lenses) stay so outside the x-ray view too.
    own.userData.seeThrough = own.transparent;
    patchMaterial(own, uniforms);
    mesh.material = own;
    materials.push(own);
  });
  const frame: Frame = { length: size.x * scale, height: size.y * scale, width: size.z * scale };
  return { root, materials, uniforms, frame };
}

const projected = new THREE.Vector3();
const offset = new THREE.Vector3();

function Car({ model, focus, dark, mode, paused, insetLeft = 0, insetRight = 0, bare, onReady, onSlow, placeLabel }: Props & { placeLabel: (x: number, y: number) => void }) {
  const gltf = useGLTF(model.file, false, true);
  const controls = useRef<OrbitControlsImpl>(null);
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const canvas = useThree((s) => s.gl.domElement);
  const invalidate = useThree((s) => s.invalidate);
  const setDpr = useThree((s) => s.setDpr);
  const [still] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const big = mode === "big";

  const rig = useMemo(() => buildRig(gltf.scene, model), [gltf, model]);
  const { root, frame } = rig;
  // three.js objects are changed in place every frame; the ref keeps those writes out of React's render data.
  const live = useRef(rig);
  const goal = useRef({ position: new THREE.Vector3(), target: new THREE.Vector3() });
  const look = useRef(new THREE.Vector3());
  // Run state that changes between frames without React needing to know.
  const run = useRef({ flying: true, placed: false, touched: false, paused, timer: 0, last: 0, late: 0, level: 0, onSlow });

  const xray = !!focus?.interior;
  const tint = focus ? (focus.tint ?? 1) : 0;
  // A new region object arrives on every render; its text form is a stable key for the effect below.
  const focusKey = focus ? JSON.stringify(focus) : "";

  useLayoutEffect(() => {
    live.current = rig;
    run.current.onSlow = onSlow;
    for (const m of live.current.materials) {
      const clear = xray || m.userData.seeThrough === true;
      m.transparent = clear;
      m.depthWrite = !clear;
    }
    invalidate();
  }, [rig, xray, onSlow, invalidate]);

  // Nothing is drawn while nobody can see the scene; one frame catches up when it is seen again.
  useEffect(() => {
    run.current.paused = paused;
    if (!paused) invalidate();
    else window.clearTimeout(run.current.timer);
  }, [paused, invalidate]);

  useEffect(() => {
    const next = focusKey ? (JSON.parse(focusKey) as Region) : null;
    const to = cameraFor(next, rig.frame, mode);
    goal.current = to;
    const r = run.current;
    if (!r.placed) {
      // First view: start where the camera belongs instead of flying in from the default position.
      r.placed = true;
      camera.position.copy(to.position);
      look.current.copy(to.target);
      camera.lookAt(to.target);
    }
    r.flying = true;
    r.touched = false;
    if (next) {
      const world = regionToWorld(next, rig.frame);
      live.current.uniforms.uZoneCenter.value.copy(world.center);
      live.current.uniforms.uZoneRadius.value.copy(world.radius);
    }
    invalidate();
  }, [focusKey, mode, rig, camera, invalidate]);

  useEffect(() => {
    onReady?.();
    invalidate();
  }, [onReady, rig, invalidate]);

  useEffect(() => {
    const view = camera as THREE.PerspectiveCamera;
    const shift = big ? (insetRight - insetLeft) / 2 : 0;
    if (shift) view.setViewOffset(size.width, size.height, shift, 0, size.width, size.height);
    else view.clearViewOffset();
    invalidate();
  }, [camera, size, big, insetLeft, insetRight, invalidate]);

  // The explorable view: the mouse wheel zooms the car.
  useEffect(() => {
    if (!big) return;
    const far = rig.frame.length * 1.8;
    const near = rig.frame.length * 0.45;
    const onWheel = (e: WheelEvent) => {
      const orbit = controls.current;
      if (!orbit) return;
      e.preventDefault();
      offset.copy(camera.position).sub(orbit.target);
      const distance = offset.length();
      run.current.flying = false;
      run.current.touched = true;
      const next = THREE.MathUtils.clamp(distance * Math.exp(e.deltaY * 0.0012), near, far);
      camera.position.copy(orbit.target).add(offset.multiplyScalar(next / distance));
      orbit.update();
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [big, canvas, camera, rig]);

  useFrame((state, dt) => {
    const r = run.current;
    const now = performance.now();
    const gap = r.last ? now - r.last : 0;
    r.last = now;
    const step = Math.min(dt, 0.1);
    const k = 1 - Math.exp(-step * 4);
    const u = live.current.uniforms;
    u.uTime.value = state.clock.elapsedTime;
    u.uGlow.value = THREE.MathUtils.lerp(u.uGlow.value, tint, k);
    u.uXray.value = THREE.MathUtils.lerp(u.uXray.value, xray ? 1 : 0, k);
    const settled = Math.abs(u.uGlow.value - tint) < 0.01 && Math.abs(u.uXray.value - (xray ? 1 : 0)) < 0.01;

    let moving = !settled;
    if (r.flying) {
      camera.position.lerp(goal.current.position, k);
      look.current.lerp(goal.current.target, k);
      if (camera.position.distanceTo(goal.current.position) < 0.02) r.flying = false;
      else moving = true;
    }
    // The idle car turns slowly, once the camera has arrived and nothing is selected or touched.
    const spinning = !r.flying && !focusKey && !still && !r.touched;
    if (spinning) {
      offset.copy(camera.position).sub(look.current).applyAxisAngle(UP, -SPIN[mode] * step);
      camera.position.copy(look.current).add(offset);
      moving = true;
    }
    if (controls.current) {
      controls.current.target.copy(look.current);
      controls.current.update();
    } else camera.lookAt(look.current);

    if (focusKey) {
      // The name tag sits just above the highlighted region.
      projected.copy(u.uZoneCenter.value);
      projected.y += u.uZoneRadius.value.y * 0.75;
      projected.project(camera);
      placeLabel((projected.x * 0.5 + 0.5) * state.size.width, (-projected.y * 0.5 + 0.5) * state.size.height);
    }

    // Ask for the next frame only if something is still moving, and not sooner than the frame rate allows.
    window.clearTimeout(r.timer);
    if (r.paused) return;
    const full = FPS[mode] / (r.level >= 1 ? 2 : 1);
    const fps = moving ? full : focusKey ? PULSE_FPS : 0;
    if (!fps) return;
    // Watch whether the frames really arrive at that rate; if they keep coming late, lower the quality.
    if (moving && gap) {
      r.late = gap > (1000 / full) * 2.2 ? r.late + 1 : Math.max(0, r.late - 1);
      if (r.late > 24 && !new URLSearchParams(location.search).has("car3d")) {
        r.late = 0;
        r.level++;
        if (r.level === 1) setDpr(0.75);
        else r.onSlow?.();
      }
    }
    r.timer = window.setTimeout(() => invalidate(), 1000 / fps);
  });

  useEffect(() => () => window.clearTimeout(run.current.timer), []);

  const disc = frame.length * 0.63;
  return (
    <>
      <primitive object={root} />
      {focus && <RegionGlow region={focus} frame={frame} />}
      {!bare && (
        <>
          <mesh position={[0, -0.04, 0]}>
            <cylinderGeometry args={[disc, disc, 0.08, 96]} />
            <meshStandardMaterial color={dark ? "#0f1215" : "#cfd5dc"} roughness={1} />
          </mesh>
          {big && (
            <mesh position={[0, 0.006, 0]} rotation={[-Math.PI / 2, 0, 0]}>
              <torusGeometry args={[disc + 0.02, 0.022, 8, 128, Math.PI * 0.55]} />
              <meshBasicMaterial color={BLAU} />
            </mesh>
          )}
        </>
      )}
      {/* Baked once: the car does not move relative to its own shadow, so one pass is enough. */}
      <ContactShadows position={[0, 0.004, 0]} scale={frame.length * 2} far={2} blur={2.4} opacity={dark ? 0.6 : 0.4} frames={1} resolution={512} />
      {big && (
        <OrbitControls
          ref={controls}
          enablePan={false}
          enableZoom={false}
          minDistance={frame.length * 0.45}
          maxDistance={frame.length * 1.8}
          maxPolarAngle={Math.PI / 2 - 0.03}
          onStart={() => {
            run.current.flying = false;
            run.current.touched = true;
          }}
        />
      )}
    </>
  );
}

/** A soft glowing volume at the region, visible through the see-through body when the model has no interior there. */
function RegionGlow({ region, frame }: { region: Region; frame: Frame }) {
  const { center, radius } = regionToWorld(region, frame);
  const mesh = useRef<THREE.Mesh>(null);
  useFrame((s) => {
    if (mesh.current) (mesh.current.material as THREE.MeshBasicMaterial).opacity = (region.interior ? 0.2 : 0.05) + 0.04 * Math.sin(s.clock.elapsedTime * 3);
  });
  return (
    <mesh ref={mesh} position={center} scale={radius.clone().multiplyScalar(0.8)} renderOrder={2}>
      <sphereGeometry args={[1, 32, 20]} />
      <meshBasicMaterial color={BLAU} transparent opacity={0.12} depthWrite={false} blending={THREE.AdditiveBlending} />
    </mesh>
  );
}

export default function CarScene(props: Props) {
  const [loaded, setLoaded] = useState<string | null>(null);
  const label = useRef<HTMLDivElement>(null);
  const key = props.model.file + props.model.length;
  const big = props.mode === "big";
  const { onReady } = props;
  const ready = useMemo(
    () => () => {
      setLoaded(key);
      onReady?.();
    },
    [key, onReady],
  );
  const placeLabel = (x: number, y: number) => {
    if (label.current) label.current.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
  };
  return (
    <div className="absolute inset-0">
      <Canvas
        frameloop="demand"
        camera={{ position: [6, 3, -7], fov: 30, near: 0.1, far: 80 }}
        // The small preview stays at one pixel per CSS pixel; only the explorable view gets the sharper screen.
        dpr={big ? [1, 1.75] : 1}
        gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
      >
        <hemisphereLight args={["#dfe8f0", props.dark ? "#0b0d10" : "#8a929c", 1.0]} />
        <directionalLight position={[5, 8, 4]} intensity={1.8} />
        <directionalLight position={[-6, 3, -5]} intensity={1.2} color="#7fc4ec" />
        {/* Studio lighting built in code: no HDR download, so the demo also runs offline. Drawn once. */}
        <Environment resolution={256} frames={1}>
          <Lightformer form="rect" intensity={3} position={[0, 6, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[12, 5, 1]} />
          <Lightformer form="rect" intensity={1.6} position={[7, 2, 3]} rotation={[0, -Math.PI / 2, 0]} scale={[8, 3, 1]} />
          <Lightformer form="rect" intensity={1.2} position={[-7, 2, -3]} rotation={[0, Math.PI / 2, 0]} scale={[8, 3, 1]} />
          <Lightformer form="rect" intensity={0.9} position={[0, 2, -8]} scale={[10, 3, 1]} />
        </Environment>
        <Suspense fallback={null}>
          <Car key={key} {...props} onReady={ready} placeLabel={placeLabel} />
        </Suspense>
      </Canvas>
      {/* The name of the highlighted part: plain DOM over the canvas, moved by the scene whenever it draws. */}
      {props.focus && loaded === key && big && (
        <div ref={label} className="pointer-events-none absolute top-0 left-0" style={{ transform: "translate(-999px, -999px)" }}>
          <div className="flex -translate-x-1/2 -translate-y-full flex-col items-center">
            <span className="rounded-full border border-white/30 bg-[#0098d4] px-3 py-1 text-[13px] font-semibold whitespace-nowrap text-white shadow-lg">{props.focus.label}</span>
            <span className="h-5 w-px bg-white/70" />
            <span className="size-1.5 rounded-full bg-white" />
          </div>
        </div>
      )}
    </div>
  );
}
