(function(){
  // ---- copy buttons ----
  function bindCopy(id){
    var b = document.getElementById(id);
    if(!b) return;
    b.addEventListener("click", function(){
      navigator.clipboard && navigator.clipboard.writeText("uv tool install tau-ai");
      var t = b.textContent; b.textContent = "copied";
      setTimeout(function(){ b.textContent = t; }, 1400);
    });
  }
  bindCopy("copyBtn"); bindCopy("copyBtn2");

  // ---- the hero: a radius sweeps the unit circle through one full turn (0 -> tau),
  //      tracing exactly one period of a sine wave on the notebook grid ----
  var canvas = document.getElementById("tauCanvas");
  if(!canvas) return;
  var ctx = canvas.getContext("2d");
  var INK   = "#13213C";
  var BLUE  = "#1B3FA0";
  var RED   = "#D6435B";
  var SOFT  = "#9FB0D0";
  var GRID  = "#C9D6EE";
  var TAU   = Math.PI * 2;

  var W = canvas.width, H = canvas.height, DPR = 1;
  function resize(){
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    var cssW = canvas.clientWidth || 520;
    var cssH = cssW * (380/520);
    canvas.width = Math.round(cssW * DPR);
    canvas.height = Math.round(cssH * DPR);
    W = canvas.width; H = canvas.height;
  }
  resize();
  window.addEventListener("resize", resize);

  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var thetaLabel = document.getElementById("thetaVal");

  // axis ticks along the wave: fractions of tau with mathy labels
  var TICKS = [
    {t: Math.PI/2,    label: "π/2"},
    {t: Math.PI,      label: "π"},
    {t: 3*Math.PI/2,  label: "3π/2"},
    {t: TAU,          label: "τ"}
  ];

  function draw(theta){
    ctx.clearRect(0,0,W,H);
    ctx.save();
    ctx.scale(DPR, DPR);
    var w = W/DPR, h = H/DPR;

    var pad = 30;
    var cx = pad + (h/2 - pad);          // circle centre x
    var cy = h/2;                         // baseline / circle centre y
    var R  = h/2 - pad - 10;             // unit radius in px
    var waveX0 = cx + R + 22;            // wave starts to the right of circle
    var waveW  = w - waveX0 - pad - 6;

    var px = cx + Math.cos(theta) * R;
    var py = cy - Math.sin(theta) * R;
    var ex = waveX0 + (theta/TAU) * waveW;
    var ey = cy - Math.sin(theta) * R;

    // --- baseline axis with arrow ---
    ctx.strokeStyle = SOFT; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad - 4, cy); ctx.lineTo(w - pad + 2, cy); ctx.stroke();
    ctx.fillStyle = SOFT;
    ctx.beginPath();
    ctx.moveTo(w - pad + 6, cy); ctx.lineTo(w - pad - 2, cy - 3.5);
    ctx.lineTo(w - pad - 2, cy + 3.5); ctx.closePath(); ctx.fill();

    // --- ghost of the full sine wave (the target) ---
    ctx.strokeStyle = GRID; ctx.lineWidth = 1; ctx.setLineDash([2,4]);
    ctx.beginPath();
    for(var g=0; g<=TAU; g+=0.05){
      var gx = waveX0 + (g/TAU) * waveW, gy = cy - Math.sin(g) * R;
      g===0 ? ctx.moveTo(gx,gy) : ctx.lineTo(gx,gy);
    }
    ctx.stroke(); ctx.setLineDash([]);

    // --- tick marks + radian labels on the wave axis ---
    ctx.font = "11px 'JetBrains Mono', monospace";
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    for(var k=0;k<TICKS.length;k++){
      var tk = TICKS[k];
      var tx = waveX0 + (tk.t/TAU) * waveW;
      var passed = theta >= tk.t - 0.001;
      ctx.strokeStyle = passed ? BLUE : GRID; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(tx, cy-4); ctx.lineTo(tx, cy+4); ctx.stroke();
      ctx.fillStyle = passed ? BLUE : SOFT;
      ctx.fillText(tk.label, tx, cy + 8);
    }

    // --- unit circle ---
    ctx.strokeStyle = GRID; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.stroke();
    // vertical diameter guide
    ctx.strokeStyle = "rgba(159,176,208,.45)"; ctx.lineWidth = 1; ctx.setLineDash([2,3]);
    ctx.beginPath(); ctx.moveTo(cx, cy-R); ctx.lineTo(cx, cy+R); ctx.stroke(); ctx.setLineDash([]);

    // --- swept angle wedge + arc ---
    ctx.fillStyle = "rgba(27,63,160,.10)";
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, R*0.42, 0, -theta, true); ctx.closePath(); ctx.fill();
    ctx.strokeStyle = BLUE; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.arc(cx, cy, R*0.42, 0, -theta, true); ctx.stroke();

    // --- traced sine wave so far (with soft glow) ---
    ctx.save();
    ctx.shadowColor = "rgba(27,63,160,.35)"; ctx.shadowBlur = 6;
    ctx.strokeStyle = BLUE; ctx.lineWidth = 2.2; ctx.lineJoin = "round";
    ctx.beginPath();
    for(var i=0; i<=theta; i+=0.02){
      var x = waveX0 + (i/TAU) * waveW;
      var y = cy - Math.sin(i) * R;
      i===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
    }
    ctx.lineTo(ex, ey); ctx.stroke();
    ctx.restore();

    // --- sweeping radius ---
    ctx.strokeStyle = INK; ctx.lineWidth = 1.8;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(px, py); ctx.stroke();

    // --- height (sin) bar on circle ---
    ctx.strokeStyle = "rgba(214,67,91,.55)"; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(px, cy); ctx.lineTo(px, py); ctx.stroke();

    // --- projection line linking circle point to wave point ---
    ctx.strokeStyle = RED; ctx.lineWidth = 1; ctx.setLineDash([3,4]);
    ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(ex, ey); ctx.stroke();
    ctx.setLineDash([]);

    // --- moving points (glow) ---
    ctx.save();
    ctx.shadowColor = "rgba(214,67,91,.5)"; ctx.shadowBlur = 8;
    ctx.fillStyle = RED;
    ctx.beginPath(); ctx.arc(px, py, 4.2, 0, TAU); ctx.fill();
    ctx.beginPath(); ctx.arc(ex, ey, 4.2, 0, TAU); ctx.fill();
    ctx.restore();
    // white pips
    ctx.fillStyle = "#fff";
    ctx.beginPath(); ctx.arc(px, py, 1.5, 0, TAU); ctx.fill();
    ctx.beginPath(); ctx.arc(ex, ey, 1.5, 0, TAU); ctx.fill();

    // centre dot
    ctx.fillStyle = INK;
    ctx.beginPath(); ctx.arc(cx, cy, 2.4, 0, TAU); ctx.fill();

    ctx.restore();

    if(thetaLabel) thetaLabel.innerHTML = "&#952; = " + theta.toFixed(2) + " rad";
  }

  if(reduce){
    draw(TAU * 0.78);
  } else {
    var theta = 0;
    var speed = TAU / 320; // ~ one turn every ~5.3s at 60fps
    function frame(){
      theta += speed;
      if(theta > TAU) theta = 0;
      draw(theta);
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  // ============================================================
  //  Concept animations for the "Why Tau?" page
  // ============================================================
  function mkCanvas(id){
    var c = document.getElementById(id);
    if(!c) return null;
    var cx = c.getContext("2d");
    var ratio = c.height / c.width;
    var w, h, dpr;
    function size(){
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      var cw = c.clientWidth || c.width;
      var ch = cw * ratio;
      c.width = Math.round(cw * dpr);
      c.height = Math.round(ch * dpr);
      w = cw; h = ch;
    }
    size();
    window.addEventListener("resize", size);
    return {
      ctx: cx,
      frame: function(fn){
        cx.setTransform(dpr, 0, 0, dpr, 0, 0);
        cx.clearRect(0, 0, w, h);
        fn(cx, w, h);
      }
    };
  }

  function loop(cv, drawAt, period){
    if(!cv) return;
    if(reduce){ cv.frame(function(ctx,w,h){ drawAt(ctx, w, h, TAU * 0.7); }); return; }
    var t = 0, sp = TAU / period;
    (function go(){
      t += sp; if(t > TAU) t = 0;
      cv.frame(function(ctx,w,h){ drawAt(ctx, w, h, t); });
      requestAnimationFrame(go);
    })();
  }

  function dot(ctx, x, y, r, fill, glow){
    if(glow){ ctx.save(); ctx.shadowColor = glow; ctx.shadowBlur = 8; }
    ctx.fillStyle = fill;
    ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
    if(glow){ ctx.restore(); }
  }

  // ---- 1. A circle unrolls: its edge is exactly tau radii long ----
  loop(mkCanvas("rollCanvas"), function(ctx, w, h, theta){
    var pad = 30;
    var R = Math.min(h * 0.34, 64);
    var baseY = h - 52;
    var startX = pad + 4;
    var travelled = R * theta;            // arc length unrolled so far
    var contactX = startX + travelled;
    var cx = contactX, cy = baseY - R;

    // ground line
    ctx.strokeStyle = SOFT; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(startX, baseY); ctx.lineTo(w - pad, baseY); ctx.stroke();

    // full track to tau*r with end tick + label
    var endX = startX + R * TAU;
    ctx.strokeStyle = GRID; ctx.setLineDash([2,4]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(startX, baseY + 14); ctx.lineTo(endX, baseY + 14); ctx.stroke();
    ctx.setLineDash([]);
    [[startX,"0"],[endX,"τ·r"]].forEach(function(m){
      ctx.strokeStyle = m[1]==="0" ? GRID : BLUE; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(m[0], baseY + 9); ctx.lineTo(m[0], baseY + 19); ctx.stroke();
      ctx.fillStyle = m[1]==="0" ? SOFT : BLUE;
      ctx.font = "12px 'JetBrains Mono', monospace";
      ctx.textAlign = m[1]==="0" ? "left" : "right"; ctx.textBaseline = "top";
      ctx.fillText(m[1], m[0] + (m[1]==="0"?0:0), baseY + 22);
    });

    // unrolled portion of the edge (thick, coloured)
    ctx.strokeStyle = BLUE; ctx.lineWidth = 3.4; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(startX, baseY); ctx.lineTo(contactX, baseY); ctx.stroke();
    ctx.lineCap = "butt";

    // the rolling circle
    ctx.strokeStyle = GRID; ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.stroke();

    // marked rim point (started at the contact point)
    var mx = cx - R * Math.sin(theta);
    var my = cy + R * Math.cos(theta);
    ctx.strokeStyle = INK; ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(mx, my); ctx.stroke();
    // "r" label on the radius
    ctx.fillStyle = INK; ctx.font = "italic 13px 'STIX Two Text', serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("r", (cx + mx)/2 + 8, (cy + my)/2 - 8);

    dot(ctx, cx, cy, 2.4, INK);
    dot(ctx, mx, my, 4, RED, "rgba(214,67,91,.5)");
    dot(ctx, contactX, baseY, 4, RED, "rgba(214,67,91,.5)");

    // readout
    ctx.fillStyle = BLUE;
    ctx.font = "13px 'JetBrains Mono', monospace";
    ctx.textAlign = "left"; ctx.textBaseline = "top";
    ctx.fillText("turns: " + (theta/TAU).toFixed(2) + "  ·  edge ≈ " + (theta).toFixed(2) + " r", startX, 38);
  }, 360);

  // ---- 2. One full turn = tau. Fractions of a turn read straight off ----
  loop(mkCanvas("angleCanvas"), function(ctx, w, h, theta){
    var cx = w * 0.5, cy = h * 0.52, R = Math.min(w, h) * 0.34;

    // circle
    ctx.strokeStyle = GRID; ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.stroke();

    // swept wedge
    ctx.fillStyle = "rgba(27,63,160,.12)";
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, R, 0, -theta, true); ctx.closePath(); ctx.fill();
    ctx.strokeStyle = BLUE; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, -theta, true); ctx.stroke();

    // quarter markers with tau labels
    var marks = [[0,"0"],[TAU/4,"τ/4"],[TAU/2,"τ/2"],[3*TAU/4,"3τ/4"]];
    ctx.font = "12px 'JetBrains Mono', monospace";
    for(var i=0;i<marks.length;i++){
      var a = marks[i][0];
      var ox = cx + Math.cos(a)*R, oy = cy - Math.sin(a)*R;
      var lx = cx + Math.cos(a)*(R+18), ly = cy - Math.sin(a)*(R+18);
      var passed = theta >= a - 0.001;
      ctx.fillStyle = passed ? BLUE : SOFT;
      ctx.beginPath(); ctx.arc(ox, oy, 2.6, 0, TAU); ctx.fill();
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(marks[i][1], lx, ly);
    }

    // radius
    var px = cx + Math.cos(theta)*R, py = cy - Math.sin(theta)*R;
    ctx.strokeStyle = INK; ctx.lineWidth = 1.8;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(px, py); ctx.stroke();
    dot(ctx, cx, cy, 2.4, INK);
    dot(ctx, px, py, 4, RED, "rgba(214,67,91,.5)");

    // centre readout
    ctx.fillStyle = BLUE; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "600 18px 'Space Grotesk', sans-serif";
    ctx.fillText((theta/TAU).toFixed(2) + " of a turn", cx, cy + R + 40);
  }, 320);

  // ---- 3. Euler: a spin by one full turn (tau) lands you back on 1 ----
  loop(mkCanvas("eulerCanvas"), function(ctx, w, h, theta){
    var cx = w * 0.5, cy = h * 0.5, R = Math.min(w, h) * 0.34;

    // axes
    ctx.strokeStyle = GRID; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx - R - 24, cy); ctx.lineTo(cx + R + 24, cy); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx, cy + R + 24); ctx.lineTo(cx, cy - R - 24); ctx.stroke();
    ctx.fillStyle = SOFT; ctx.font = "11px 'JetBrains Mono', monospace";
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText("Re", cx + R + 8, cy + 10);
    ctx.textAlign = "center"; ctx.fillText("Im", cx + 12, cy - R - 16);

    // unit circle
    ctx.strokeStyle = GRID; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.stroke();

    // the landing point "1"
    var near = Math.min(theta, TAU - theta) < 0.18;
    ctx.fillStyle = near ? BLUE : SOFT;
    dot(ctx, cx + R, cy, near ? 5 : 3.2, near ? BLUE : SOFT, near ? "rgba(27,63,160,.5)" : null);
    ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.font = "12px 'JetBrains Mono', monospace";
    ctx.fillText("1", cx + R, cy + 8);

    // rotating phasor e^{i theta}
    var px = cx + Math.cos(theta)*R, py = cy - Math.sin(theta)*R;
    // projections
    ctx.strokeStyle = "rgba(159,176,208,.6)"; ctx.lineWidth = 1; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px, cy); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(cx, py); ctx.stroke();
    ctx.setLineDash([]);
    // arc swept
    ctx.strokeStyle = BLUE; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.arc(cx, cy, R*0.32, 0, -theta, true); ctx.stroke();

    ctx.strokeStyle = INK; ctx.lineWidth = 1.8;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(px, py); ctx.stroke();
    dot(ctx, cx, cy, 2.4, INK);
    dot(ctx, px, py, 4.2, RED, "rgba(214,67,91,.5)");

    // label
    ctx.fillStyle = near ? BLUE : INK;
    ctx.font = "italic 14px 'STIX Two Text', serif";
    ctx.textAlign = "left"; ctx.textBaseline = "bottom";
    var lbl = near ? "e^{iτ} = 1" : "e^{iθ}";
    ctx.fillText(lbl, px + 10, py - 6);
  }, 340);
})();
