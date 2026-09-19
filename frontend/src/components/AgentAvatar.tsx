import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { useReducedMotion } from '@/lib/motion'

// Scene props the GLB ships with (desk, keyboard, ground, floating hands, legs). Matched by the exact
// glTF node name, which GLTFLoader keeps verbatim in userData.name. Object3D.name is unreliable here:
// it is sanitized ("Face.002" -> "Face002") and de-duplicated, and the prop nodes "Plane"/"Plane.002"
// collide with the eyebrow/ear mesh names, so they can come out as "Plane_1" depending on load order.
const nodeName = (n: string) => THREE.PropertyBinding.sanitizeNodeName(n)
const HIDDEN = new Set(['Cube.002', 'screenlight', 'Keyboard', 'Plane', 'ground', 'Plane.002', 'Plane.003', 'Plane.004', 'Hand', 'Pant', 'Shoe', 'Sole'])
const SHIRT = 'BODY.SHIRT'

// The face has eye morphs but no mouth shape, so build one: everything below `line` (chin, lower lip,
// lower teeth) drops by up to `amount`, eased so the cheeks stay put. Relative morph on a skinned mesh.
function addJawMorph(mesh: THREE.Mesh, line: number, depth: number, amount: number) {
  const geo = mesh.geometry
  const pos = geo.attributes.position as THREE.BufferAttribute
  const delta = new Float32Array(pos.count * 3)
  let touched = 0
  for (let i = 0; i < pos.count; i++) {
    const y = pos.getY(i), z = pos.getZ(i)
    if (y >= line || z < -0.3) continue
    const w = Math.sin(Math.min(1, (line - y) / depth) * Math.PI * 0.5)
    delta[i * 3 + 1] = -amount * w
    delta[i * 3 + 2] = -amount * 0.2 * w
    touched++
  }
  if (!touched) return null
  geo.morphAttributes.position = geo.morphAttributes.position ?? []
  geo.morphAttributes.position.push(new THREE.Float32BufferAttribute(delta, 3))
  // The GLB's morphs also carry normal (and possibly color) deltas. WebGLMorphtargets packs every
  // target into one texture and indexes normal/color by the position index, so the arrays must stay
  // the same length or the renderer throws on the first frame. Push zero deltas alongside.
  for (const key of ['normal', 'color'] as const) {
    const arr = geo.morphAttributes[key]
    if (arr && arr.length) arr.push(new THREE.Float32BufferAttribute(new Float32Array(pos.count * arr[0].itemSize), arr[0].itemSize))
  }
  geo.morphTargetsRelative = true
  mesh.updateMorphTargets()
  return geo.morphAttributes.position.length - 1
}

// The rig is exported in a T-pose. Swing each upper arm so it hangs at the side: find the world direction
// from the shoulder joint to the elbow and rotate the bone so that direction points down and slightly out.
function lowerArm(model: THREE.Object3D, side: 'L' | 'R') {
  const upper = model.getObjectByName(nodeName(`upper_arm.${side}`))
  const fore = model.getObjectByName(nodeName(`forearm.${side}`))
  if (!upper || !fore || !upper.parent) return
  model.updateMatrixWorld(true)
  const a = upper.getWorldPosition(new THREE.Vector3())
  const b = fore.getWorldPosition(new THREE.Vector3())
  const from = b.sub(a).normalize()
  const to = new THREE.Vector3(side === 'L' ? 0.22 : -0.22, -1, 0.05).normalize()
  const swing = new THREE.Quaternion().setFromUnitVectors(from, to)
  const world = upper.getWorldQuaternion(new THREE.Quaternion()).premultiply(swing)
  const parentInv = upper.parent.getWorldQuaternion(new THREE.Quaternion()).invert()
  upper.quaternion.copy(parentInv.multiply(world))
}

export function AgentAvatar({ className, zoomOut = false, isSpeaking = false, isListening = false, level }: {
  className?: string; zoomOut?: boolean; isSpeaking?: boolean; isListening?: boolean
  /** Live voice loudness 0..1 (from an AnalyserNode). When present and speaking, drives the mouth instead of the synthetic rhythm. */
  level?: React.MutableRefObject<number>
}) {
  const host = useRef<HTMLDivElement>(null)
  const reduced = useReducedMotion()
  // Read by the render loop each frame, so toggling them never rebuilds the scene or reloads the model.
  const state = useRef({ isSpeaking, isListening, level, reduced, zoomOut })
  state.current.isSpeaking = isSpeaking
  state.current.isListening = isListening
  state.current.level = level
  state.current.reduced = reduced
  state.current.zoomOut = zoomOut

  useEffect(() => {
    const el = host.current
    if (!el) return

    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      // No WebGL (headless, blocked GPU, exhausted contexts): leave the host empty rather than crash the page.
      return
    }
    // Cap the ratio: 3x phones would render 9x the pixels for no visible gain on a small panel.
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))

    const scene = new THREE.Scene()
    // Face-forward close-up: fill the frame with the face like a video call
    const EYE_LINE = 13.8
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 1000)
    camera.position.set(0, EYE_LINE, 6.5)

    const updateSize = () => {
      const rect = el.getBoundingClientRect()
      const w = Math.max(1, Math.floor(rect.width)), h = Math.max(1, Math.floor(rect.height))
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      // Re-read the DPR every resize: browser zoom or moving the window to a differently scaled monitor
      // changes it, and a stale ratio renders the canvas blurry (zoomed in) or oversampled (zoomed out).
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
      renderer.setSize(w, h, false)
    }
    updateSize()
    // The panel resizes when the playground stacks on mobile or a sibling column collapses, not only on
    // window resize; observe the host itself. Fall back to the window event where ResizeObserver is missing.
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(updateSize) : null
    if (ro) ro.observe(el)
    else window.addEventListener('resize', updateSize)
    renderer.domElement.style.width = '100%'
    renderer.domElement.style.height = '100%'
    renderer.domElement.style.display = 'block'
    el.appendChild(renderer.domElement)

    // Lighting (Portfolio matched)
    const directionalLight = new THREE.DirectionalLight(0x5eead4, 1)
    directionalLight.position.set(-0.47, -0.32, -1)
    scene.add(directionalLight)

    const pointLight = new THREE.PointLight(0x22d3ee, 50, 100, 3)
    pointLight.position.set(3, 12, 4)
    scene.add(pointLight)

    const fillLight = new THREE.DirectionalLight(0xffffff, 1.5)
    fillLight.position.set(0, 0, 5)
    scene.add(fillLight)

    // Cyan/Teal rim light from bottom left
    const cyanRim = new THREE.DirectionalLight(0x00ffff, 2.0)
    cyanRim.position.set(-5, -2, 2)
    scene.add(cyanRim)

    const group = new THREE.Group()
    scene.add(group)

    let mixer: THREE.AnimationMixer | null = null
    let face: THREE.Mesh | null = null
    let jawIndex: number | null = null
    let teeth: THREE.Mesh | undefined
    let teethIndex: number | null = null
    let head: THREE.Object3D | undefined
    let blinkAction: THREE.AnimationAction | null = null

    // Dispose every geometry/material/texture under a subtree. Used both on teardown and when the GLB
    // resolves after teardown, so a scene that never reached the renderer still frees its GPU buffers.
    const disposeTree = (root: THREE.Object3D) => {
      root.traverse((o: any) => {
        if (!o.isMesh) return
        o.geometry?.dispose()
        for (const m of ([] as THREE.Material[]).concat(o.material ?? [])) {
          for (const v of Object.values(m)) if ((v as any)?.isTexture) (v as any).dispose()
          m.dispose()
        }
      })
    }

    // Fade the model in once it is added: the 2.3 MB GLB takes seconds on mobile networks and would
    // otherwise pop into an empty panel in a single frame. Reduced motion skips the ramp.
    renderer.domElement.style.opacity = '0'
    renderer.domElement.style.transition = state.current.reduced ? 'none' : 'opacity 600ms ease-out'

    let running = true
    const loader = new GLTFLoader()
    loader.load('/models/character.glb', (gltf) => {
      // The effect can be torn down (route change, StrictMode double-mount) before the GLB arrives.
      // The parsed scene never reaches `scene`, so the teardown traversal cannot free it; do it here.
      if (!running) { disposeTree(gltf.scene); return }
      const model = gltf.scene
      model.traverse((child: any) => {
        const original: string | undefined = child.userData?.name
        if (original && HIDDEN.has(original)) child.visible = false
        // The shirt's materials are near-black (base colour 0.01) and the panel behind it is dark, so it
        // rendered as nothing and the head looked cut off at the neck. Lift it to a charcoal that reads.
        if (original === SHIRT) {
          child.traverse((m: any) => {
            if (!m.isMesh) return
            const mats: THREE.Material[] = Array.isArray(m.material) ? m.material : [m.material]
            for (const mat of mats) {
              const std = mat as THREE.MeshStandardMaterial
              if (std.color) std.color.setRGB(0.13, 0.14, 0.17)
              if (std.emissive) std.emissive.setRGB(0, 0, 0)
              std.roughness = 0.9
              std.metalness = 0
            }
          })
        }
      })

      face = model.getObjectByName(nodeName('Face.002')) as THREE.Mesh | null
      if (face?.isMesh) jawIndex = addJawMorph(face, 12.55, 0.7, 0.36)
      // Teeth are one block; dropping its lower half reads as the mouth opening between the rows.
      teeth = model.getObjectByName(nodeName('Teeth.001')) as THREE.Mesh | undefined
      if (teeth?.isMesh) teethIndex = addJawMorph(teeth, 12.55, 0.35, 0.3)
      head = model.getObjectByName(nodeName('spine.006'))
      lowerArm(model, 'L')
      lowerArm(model, 'R')

      group.add(model)
      el.dataset.loaded = 'true'
      renderer.domElement.style.opacity = '1'

      // ORDER MATTERS: the Blink clip binds Face.002.morphTargetInfluences as an EntireArray of 3 values,
      // but addJawMorph above made the array 4 long, so every mixer.update() writes buffer garbage into
      // the jaw slot. The render loop overwrites morphTargetInfluences[jawIndex] AFTER mixer.update()
      // in the same frame, which is what keeps the mouth correct. Do not reorder those two steps.
      mixer = new THREE.AnimationMixer(model)
      const blink = gltf.animations.find((c) => c.name === 'Blink')
      // Blinking is motion too. Always start the action and pause/unpause it per frame below, so the
      // in-app motion toggle (and prefers-reduced-motion) applies live like every other motion here.
      if (blink) {
        blinkAction = mixer.clipAction(blink)
        blinkAction.paused = state.current.reduced
        blinkAction.play()
      }
    }, undefined, (err) => {
      // A missing/blocked model must not surface as an unhandled rejection; the empty panel is the fallback.
      console.warn('AgentAvatar: could not load /models/character.glb', err)
    })

    const clock = new THREE.Clock()
    let raf = 0
    let mouth = 0
    // Once the analyser has reported real loudness during a speaking bout, trust it for the rest of that
    // bout, including the ~0 readings between words and sentences, so pauses close the mouth. Only when
    // no live level ever arrives (analyser unsupported/blocked) do we fall back to the synthetic rhythm.
    let analyserLive = false
    // Pause rendering while the panel is scrolled out of view or covered: a constant 60 fps at 2x DPR is a
    // real battery/thermal cost on phones and nobody can see the result.
    let visible = true

    const frame = () => {
      if (!running) return
      if (!visible) { raf = 0; return }
      const dt = Math.min(clock.getDelta(), 0.1)
      const t = clock.elapsedTime
      const { isSpeaking, isListening, level, reduced, zoomOut } = state.current

      // See the ORDER MATTERS note where the mixer is created: the jaw influence is written below,
      // after this update, on purpose.
      if (blinkAction && blinkAction.paused !== reduced) blinkAction.paused = reduced
      mixer?.update(dt)

      // Zoomed out shows the shoulders as well; ease the camera so toggling it does not cut.
      // Under reduced motion snap instead of easing so nothing glides.
      const camZ = zoomOut ? 8.5 : 6.5
      camera.position.z = reduced ? camZ : THREE.MathUtils.lerp(camera.position.z, camZ, 0.08)

      // Listen: lean in and cock the head slightly. Idle: a slow breathing sway so it never looks frozen.
      const targetZ = isListening ? 1.5 : 0
      group.position.z = reduced ? targetZ : THREE.MathUtils.lerp(group.position.z, targetZ, 0.05)
      if (head) {
        const sway = reduced ? 0 : Math.sin(t * 0.7) * 0.02
        const nod = isSpeaking && !reduced ? Math.sin(t * 2.3) * 0.025 : 0
        const ry = (isListening ? 0.15 : 0) + sway
        const rz = isListening ? 0.08 : 0
        head.rotation.y = reduced ? ry : THREE.MathUtils.lerp(head.rotation.y, ry, 0.05)
        head.rotation.z = reduced ? rz : THREE.MathUtils.lerp(head.rotation.z, rz, 0.05)
        head.rotation.x = reduced ? nod : THREE.MathUtils.lerp(head.rotation.x, nod, 0.1)
      }

      // Mouth: follow the real voice level when audio is playing; otherwise layered sines that read as
      // syllables rather than a metronome. Snaps shut when done.
      if (!isSpeaking) analyserLive = false
      const lv = level?.current
      const hasLevel = typeof lv === 'number' && Number.isFinite(lv)
      if (isSpeaking && hasLevel && lv > 0.02) analyserLive = true
      const target = !isSpeaking || reduced ? 0
        : analyserLive && hasLevel ? Math.min(1, Math.max(0, lv) * 2.2)
        : Math.max(0, 0.55 + 0.45 * Math.sin(t * 14) * Math.sin(t * 5.3 + 1) + 0.25 * Math.sin(t * 23))
      mouth = THREE.MathUtils.lerp(mouth, target, target > mouth ? 0.5 : 0.25)
      if (face && jawIndex !== null && face.morphTargetInfluences) face.morphTargetInfluences[jawIndex] = mouth
      if (teeth && teethIndex !== null && teeth.morphTargetInfluences) teeth.morphTargetInfluences[teethIndex] = mouth

      camera.lookAt(0, EYE_LINE, 0)
      renderer.render(scene, camera)
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    const io = typeof IntersectionObserver !== 'undefined'
      ? new IntersectionObserver((entries) => {
          const now = entries.some((e) => e.isIntersecting)
          if (now === visible) return
          visible = now
          if (visible && running && !raf) {
            clock.getDelta() // drop the time spent hidden so the first frame back does not jump
            raf = requestAnimationFrame(frame)
          }
        })
      : null
    if (io) io.observe(el)

    return () => {
      running = false
      if (ro) ro.disconnect()
      else window.removeEventListener('resize', updateSize)
      if (io) io.disconnect()
      cancelAnimationFrame(raf)
      mixer?.stopAllAction()
      // Free GPU memory and the WebGL context; browsers cap contexts, and route changes remount this.
      disposeTree(scene)
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
    }
    // Props are read through `state` each frame, so nothing here should rebuild the scene or refetch the GLB.
  }, [])

  return (
    <div ref={host} aria-hidden="true" className={className} style={{ width: '100%', height: '100%', minWidth: 0, minHeight: 0, overflow: 'hidden', pointerEvents: 'none' }} />
  )
}
