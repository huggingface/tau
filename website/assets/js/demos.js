// Autoplay silent demos only while visible. Native controls remain the fallback.
(() => {
  const videos = [...document.querySelectorAll(".demo-recording video")];
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const states = new Map(videos.map(video => [video, {
    visible: false,
    userPaused: false,
    automaticPause: false,
    starting: false
  }]));

  function pause(video, state) {
    if (!video.paused) {
      state.automaticPause = true;
      video.pause();
    }
  }

  function sync(video, state) {
    if (!state.visible || document.hidden) {
      pause(video, state);
      return;
    }
    if (motion.matches || state.userPaused || state.starting || video.ended || !video.paused) return;
    state.starting = true;
    video.play().then(() => {
      // Visibility/preferences can change while the first frames are loading.
      if (!state.visible || document.hidden || motion.matches || state.userPaused) pause(video, state);
    }).catch(() => {
      // Autoplay can be blocked by browser policy. Leave the native play button available.
    }).finally(() => {
      state.starting = false;
    });
  }

  videos.forEach(video => {
    const state = states.get(video);
    video.addEventListener("pause", () => {
      if (state.automaticPause) state.automaticPause = false;
      else if (!video.ended) state.userPaused = true;
    });
    video.addEventListener("play", () => {
      state.userPaused = false;
    });
  });

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        const state = states.get(entry.target);
        state.visible = entry.isIntersecting && entry.intersectionRatio >= 0.35;
        sync(entry.target, state);
      });
    }, { threshold: [0, 0.35] });
    videos.forEach(video => observer.observe(video));
  }

  document.addEventListener("visibilitychange", () => {
    states.forEach((state, video) => sync(video, state));
  });
  motion.addEventListener("change", () => {
    states.forEach((state, video) => {
      if (motion.matches) pause(video, state);
      else sync(video, state);
    });
  });
})();
