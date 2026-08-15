/* QuickBite AI + Loyalty — hero constellation mesh.
   Vanilla Canvas 2D port of a mouse-reactive spring-mass node network,
   scoped to the hero section's own bounds (not the full viewport) and
   paused whenever the hero scrolls off screen. No React/build step needed —
   this project renders server-side with Jinja2, so the component's actual
   logic (canvas physics, not its React wrapper) is what got ported.
   Respects prefers-reduced-motion: draws one static frame, no loop. */
(function () {
  'use strict';

  var canvas = document.getElementById('hero-canvas');
  if (!canvas || !canvas.getContext) return;

  var hero = canvas.closest('.hero');
  if (!hero) return;

  var ctx = canvas.getContext('2d', { alpha: false });
  if (!ctx) return;

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var BG_COLOR = '#0b0b0d';
  var NODE_RGB = '245, 245, 247';
  var ACCENT_RGB = '96, 165, 250';
  var SPACING = 64;
  var MAX_CONN_DIST = 88;
  var MAX_CONN_DIST_SQ = MAX_CONN_DIST * MAX_CONN_DIST;
  var SPRING_K = 18;
  var DAMPING = 0.82;

  var width = 0;
  var height = 0;
  var nodes = [];
  var rafId = null;
  var running = false;
  var lastTime = 0;

  var mouse = { x: -1000, y: -1000, prevX: -1000, prevY: -1000, vx: 0, vy: 0, radius: 200 };

  function initNodes() {
    nodes = [];
    var cols = Math.ceil(width / SPACING) + 1;
    var rows = Math.ceil(height / SPACING) + 1;
    for (var i = 0; i < cols; i++) {
      for (var j = 0; j < rows; j++) {
        var x = i * SPACING;
        var y = j * SPACING;
        nodes.push({
          x: x,
          y: y,
          vx: 0,
          vy: 0,
          baseX: x,
          baseY: y,
          radius: Math.random() * 1.1 + 1.1,
          pulse: Math.random() * Math.PI * 2
        });
      }
    }
  }

  function drawConnections(alphaScale) {
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      for (var j = i + 1; j < nodes.length; j++) {
        var n2 = nodes[j];
        var dx = n.x - n2.x;
        var dy = n.y - n2.y;
        var distSq = dx * dx + dy * dy;
        if (distSq < MAX_CONN_DIST_SQ) {
          var d = Math.sqrt(distSq);
          var alpha = (1 - d / MAX_CONN_DIST) * alphaScale;
          ctx.strokeStyle = 'rgba(' + NODE_RGB + ', ' + alpha + ')';
          ctx.lineWidth = 0.7;
          ctx.beginPath();
          ctx.moveTo(n.x, n.y);
          ctx.lineTo(n2.x, n2.y);
          ctx.stroke();
        }
      }
    }
  }

  function drawStaticFrame() {
    ctx.fillStyle = BG_COLOR;
    ctx.fillRect(0, 0, width, height);
    drawConnections(0.16);
    for (var k = 0; k < nodes.length; k++) {
      var n = nodes[k];
      ctx.fillStyle = 'rgba(' + NODE_RGB + ', 0.3)';
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function resize() {
    width = hero.clientWidth;
    height = hero.clientHeight;
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    // setTransform (absolute) rather than ctx.scale (cumulative) so repeated
    // resizes never compound the DPR scale factor.
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    initNodes();
    drawStaticFrame();
  }

  function updateMouseFromEvent(e) {
    var rect = canvas.getBoundingClientRect();
    mouse.x = e.clientX - rect.left;
    mouse.y = e.clientY - rect.top;
  }

  function onMouseLeave() {
    mouse.x = -1000;
    mouse.y = -1000;
  }

  function render(now) {
    if (!running) return;
    var dt = Math.min((now - lastTime) / 1000, 0.05) || 0.016;
    lastTime = now;

    mouse.vx = (mouse.x - mouse.prevX) / (dt * 1000 || 1);
    mouse.vy = (mouse.y - mouse.prevY) / (dt * 1000 || 1);
    mouse.prevX = mouse.x;
    mouse.prevY = mouse.y;
    var speed = Math.sqrt(mouse.vx * mouse.vx + mouse.vy * mouse.vy);

    ctx.fillStyle = BG_COLOR;
    ctx.fillRect(0, 0, width, height);

    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      n.pulse += dt * 3;

      var dx = mouse.x - n.x;
      var dy = mouse.y - n.y;
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < mouse.radius && dist > 0) {
        var power = 1 - dist / mouse.radius;
        var force = power * (1200 + speed * 120);
        var angle = Math.atan2(dy, dx);
        n.vx -= Math.cos(angle) * force * dt;
        n.vy -= Math.sin(angle) * force * dt;
      }

      var homeDx = n.baseX - n.x;
      var homeDy = n.baseY - n.y;
      n.vx += homeDx * SPRING_K * dt;
      n.vy += homeDy * SPRING_K * dt;
      n.vx *= DAMPING;
      n.vy *= DAMPING;
      n.x += n.vx * dt * 60;
      n.y += n.vy * dt * 60;
    }

    drawConnections(0.18);

    for (var c = 0; c < nodes.length; c++) {
      var node = nodes[c];
      var mdx = mouse.x - node.x;
      var mdy = mouse.y - node.y;
      var mdist = Math.sqrt(mdx * mdx + mdy * mdy);
      var isNear = mdist < mouse.radius;
      var baseAlpha = isNear ? 0.9 : 0.22 + Math.sin(node.pulse) * 0.08;

      ctx.fillStyle = isNear
        ? 'rgba(' + ACCENT_RGB + ', ' + baseAlpha + ')'
        : 'rgba(' + NODE_RGB + ', ' + baseAlpha + ')';
      var r = isNear ? node.radius * 2 : node.radius + Math.sin(node.pulse) * 0.25;
      ctx.beginPath();
      ctx.arc(node.x, node.y, Math.max(0.5, r), 0, Math.PI * 2);
      ctx.fill();
    }

    rafId = requestAnimationFrame(render);
  }

  function start() {
    if (running || reduceMotion) return;
    running = true;
    lastTime = performance.now();
    rafId = requestAnimationFrame(render);
  }

  function stop() {
    running = false;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  }

  resize();

  if (reduceMotion) {
    window.addEventListener('resize', resize);
    return;
  }

  if (window.ResizeObserver) {
    new ResizeObserver(resize).observe(hero);
  } else {
    window.addEventListener('resize', resize);
  }

  window.addEventListener('mousemove', updateMouseFromEvent, { passive: true });
  window.addEventListener('mouseleave', onMouseLeave);

  if (window.IntersectionObserver) {
    new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) start();
          else stop();
        });
      },
      { threshold: 0 }
    ).observe(hero);
  } else {
    start();
  }
})();
