<script lang="ts">

  import { onMount } from 'svelte'
  import { fade, fly } from 'svelte/transition'

  export let mask = true
  export let id = "canvas"
  export let speed = 0.04
  let canvas:any, ctx:any
  let width = 20
  let height = 20
  $: innerWidth = 0
  $: show = true

  // $: if(innerWidth >= 1128) {
  //   show = true
  // } else {
  //   show = false
  // }
  
  let frame = 0
  let stopped = false

  const draw = (el: HTMLCanvasElement) => {
    canvas = el
    ctx = canvas.getContext("2d")
    let time = 0
    
    const color = function (x: number, y: number, r: number, g: number, b: number) {
      ctx.fillStyle = `rgb(${r}, ${g}, ${b})`
      ctx.fillRect(x, y, 5, 5);
    }

    const R = function (x: number, y: number, time: number) {
      return (Math.floor(150 + 64 * Math.cos((x * x - y * y) / 300 + time)))
    }

    const G = function (x: number, y: number, time: number) {
      return (Math.floor(130 + 64 * Math.sin((x * x * Math.cos(time / 4) + y * y * Math.sin(time / 3)) / 300)))
    }

    const B = function (x: number, y: number, time: number) {
      return (Math.floor(200 + 64 * Math.sin(5 * Math.sin(time / 9) + ((x - 100) * (x - 100) + (y - 100) * (y - 100)) / 1100)))
    }

    const startAnimation = function () {
      let x: number, y: number
      for (x = 0; x <= 30; x++) {
        for (y = 0; y <= 30; y++) {
          color(x, y, R(x, y, time), G(x, y, time), B(x, y, time))
        }
      }
      // time = time + 0.01;
      // time = time + 0.025
      time = time + speed
      if (!stopped) frame = window.requestAnimationFrame(startAnimation)
    }
    startAnimation()
  }

  // Draws into this component's own canvas, and stops the loop when it unmounts
  // (otherwise every call would leave an animation running in the background).
  function init(el: HTMLCanvasElement) {
    draw(el)
    return {
      destroy() {
        stopped = true
        window.cancelAnimationFrame(frame)
      },
    }
  }

</script>

<svelte:window bind:innerWidth />

{#if show}
<div class="canvas" in:fade={{duration: 250}}>
  <div class="canvas-holder" class:mask={mask}>
    <canvas id={id} width={width} height={height} use:init></canvas>
  </div>
</div>
{/if}

<style lang="scss">


  canvas {
    width: 100%;
    height: 100%;
    position: absolute;
    left: 0;
    top: 0;
    z-index: 1;
    opacity: 1;
    filter: saturate(2);
    
  }

  .canvas-holder {
    --_width: var(--width, 100%);
    --_height: var(--height, 100%);
    width: var(--_width);
    height: var(--_height);
    position: relative;
    z-index: 1;
  }

  .canvas {
    --_width: var(--width, 100%);
    --_height: var(--height, 100%);
    --_position: var(--position, relative);
    position: var(--_position);
    z-index: 1;
    overflow: hidden;
    display: inline-flex;
    top: -1px;
    left: -1px;
  }

</style>