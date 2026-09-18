/**
 * The agent's voice as a 3D particle sphere (three.js, loaded lazily — see Orb3D in VoiceViz).
 *
 * ~2,800 points on a sphere, displaced on the GPU by 3D noise. `state` sets how much energy the
 * surface carries: idle barely ripples, a live call makes it swell and churn. Energy eases towards
 * its target, so a call starting reads as the orb waking up rather than snapping.
 *
 * Cost control: it renders only while on screen and while the tab is visible, caps pixel ratio
 * at 2, and draws a single still frame when motion is reduced.
 */

import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { useReducedMotion } from '@/lib/motion'

export type Orb3DState = 'idle' | 'listening' | 'speaking' | 'live'

const ENERGY: Record<Orb3DState, number> = { idle: 0.12, listening: 0.26, live: 0.55, speaking: 0.8 }
const SPIN: Record<Orb3DState, number> = { idle: 0.08, listening: 0.14, live: 0.28, speaking: 0.4 }

const vertex = /* glsl */ `
  uniform float uTime;
  uniform float uEnergy;
  uniform float uSize;
  varying float vGlow;
  varying vec3 vPos;

  // Ashima 3D simplex noise (public domain), trimmed.
  vec4 permute(vec4 x) { return mod(((x * 34.0) + 1.0) * x, 289.0); }
  vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
  float snoise(vec3 v) {
    const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
    const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
    vec3 i = floor(v + dot(v, C.yyy));
    vec3 x0 = v - i + dot(i, C.xxx);
    vec3 g = step(x0.yzx, x0.xyz);
    vec3 l = 1.0 - g;
    vec3 i1 = min(g.xyz, l.zxy);
    vec3 i2 = max(g.xyz, l.zxy);
    vec3 x1 = x0 - i1 + C.xxx;
    vec3 x2 = x0 - i2 + 2.0 * C.xxx;
    vec3 x3 = x0 - 1.0 + 3.0 * C.xxx;
    i = mod(i, 289.0);
    vec4 p = permute(permute(permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
    float n_ = 1.0 / 7.0;
    vec3 ns = n_ * D.wyz - D.xzx;
    vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
    vec4 x_ = floor(j * ns.z);
    vec4 y_ = floor(j - 7.0 * x_);
    vec4 x = x_ * ns.x + ns.yyyy;
    vec4 y = y_ * ns.x + ns.yyyy;
    vec4 h = 1.0 - abs(x) - abs(y);
    vec4 b0 = vec4(x.xy, y.xy);
    vec4 b1 = vec4(x.zw, y.zw);
    vec4 s0 = floor(b0) * 2.0 + 1.0;
    vec4 s1 = floor(b1) * 2.0 + 1.0;
    vec4 sh = -step(h, vec4(0.0));
    vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
    vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
    vec3 p0 = vec3(a0.xy, h.x);
    vec3 p1 = vec3(a0.zw, h.y);
    vec3 p2 = vec3(a1.xy, h.z);
    vec3 p3 = vec3(a1.zw, h.w);
    vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
    p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
    vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
    m = m * m;
    return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
  }

  void main() {
    float t = uTime * (0.35 + uEnergy * 0.9);
    float n = snoise(position * 1.6 + vec3(t)) * 0.6 + snoise(position * 3.2 - vec3(t * 1.3)) * 0.4;
    vec3 displaced = position * (1.0 + n * (0.08 + uEnergy * 0.32));
    vGlow = clamp(0.35 + n * 0.9, 0.0, 1.0);
    vPos = normalize(position);
    vec4 mv = modelViewMatrix * vec4(displaced, 1.0);
    gl_PointSize = uSize * (0.7 + vGlow * 0.9) * (3.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`

const fragment = /* glsl */ `
  uniform float uTime;
  uniform vec3 uColorA;
  uniform vec3 uColorB;
  uniform vec3 uColorC;
  varying float vGlow;
  varying vec3 vPos;

  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float soft = smoothstep(0.5, 0.0, d);
    float band = 0.5 + 0.5 * sin(vPos.y * 3.0 + uTime * 0.6);
    vec3 color = mix(mix(uColorA, uColorB, band), uColorC, smoothstep(0.55, 1.0, vGlow));
    gl_FragColor = vec4(color, soft * (0.35 + vGlow * 0.65));
  }
`

function fibonacciSphere(count: number, radius: number) {
  const positions = new Float32Array(count * 3)
  const golden = Math.PI * (3 - Math.sqrt(5))
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2
    const r = Math.sqrt(1 - y * y)
    const theta = golden * i
    positions.set([Math.cos(theta) * r * radius, y * radius, Math.sin(theta) * r * radius], i * 3)
  }
  return positions
}

export default function VoiceOrb3D({ state = 'idle', size = 140, className, onUnsupported }: {
  state?: Orb3DState; size?: number; className?: string; onUnsupported?: () => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const target = useRef(state)
  const reduced = useReducedMotion()
  target.current = state

  useEffect(() => {
    const el = host.current
    if (!el) return
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' })
    } catch {
      onUnsupported?.()
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setSize(size, size)
    el.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 20)
    camera.position.z = 3.4

    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(fibonacciSphere(size > 180 ? 4200 : 2800, 1), 3))
    const uniforms = {
      uTime: { value: 0 },
      uEnergy: { value: ENERGY[target.current] },
      uSize: { value: (size > 180 ? 7 : 5.5) * Math.min(window.devicePixelRatio || 1, 2) },
      uColorA: { value: new THREE.Color('#6d7cff') },
      uColorB: { value: new THREE.Color('#22d3a6') },
      uColorC: { value: new THREE.Color('#f472b6') },
    }
    const material = new THREE.ShaderMaterial({
      vertexShader: vertex, fragmentShader: fragment, uniforms,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    })
    const points = new THREE.Points(geometry, material)
    scene.add(points)

    // A faint wire shell turning the other way gives the sphere depth without more particles.
    const shellGeometry = new THREE.IcosahedronGeometry(1.32, 1)
    const shellMaterial = new THREE.MeshBasicMaterial({ color: '#8b93ff', wireframe: true, transparent: true, opacity: 0.07 })
    const shell = new THREE.Mesh(shellGeometry, shellMaterial)
    scene.add(shell)

    // Tilt towards the pointer, eased, so the orb feels aware of you without chasing the cursor.
    const tilt = { x: 0, y: 0, tx: 0, ty: 0 }
    const onMove = (e: PointerEvent) => {
      tilt.ty = (e.clientX / window.innerWidth - 0.5) * 0.6
      tilt.tx = (e.clientY / window.innerHeight - 0.5) * 0.4
    }
    window.addEventListener('pointermove', onMove, { passive: true })

    let visible = true
    const io = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; if (visible) loop() })
    io.observe(el)
    const onVisibility = () => { if (!document.hidden) loop() }
    document.addEventListener('visibilitychange', onVisibility)

    const clock = new THREE.Clock()
    let raf = 0
    let running = false
    const frame = () => {
      const dt = Math.min(clock.getDelta(), 0.05)
      uniforms.uTime.value += dt
      uniforms.uEnergy.value += (ENERGY[target.current] - uniforms.uEnergy.value) * Math.min(1, dt * 2.5)
      const spin = SPIN[target.current]
      points.rotation.y += dt * spin
      shell.rotation.y -= dt * spin * 0.6
      shell.rotation.x += dt * spin * 0.3
      tilt.x += (tilt.tx - tilt.x) * Math.min(1, dt * 3)
      tilt.y += (tilt.ty - tilt.y) * Math.min(1, dt * 3)
      scene.rotation.x = tilt.x
      scene.rotation.z = -tilt.y * 0.3
      renderer.render(scene, camera)
    }
    function loop() {
      if (running || reduced) return
      running = true
      const tick = () => {
        if (!visible || document.hidden) { running = false; return }
        frame()
        raf = requestAnimationFrame(tick)
      }
      raf = requestAnimationFrame(tick)
    }
    if (reduced) frame()
    else loop()

    return () => {
      cancelAnimationFrame(raf)
      io.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pointermove', onMove)
      geometry.dispose(); material.dispose(); shellGeometry.dispose(); shellMaterial.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [size, reduced, onUnsupported])

  return <div ref={host} className={className} style={{ width: size, height: size }} aria-hidden />
}
