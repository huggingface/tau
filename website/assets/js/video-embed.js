document.addEventListener("click", function (event) {
  const button = event.target.closest(".video-demo-play");
  if (!button) return;

  const player = button.closest(".video-demo-player");
  if (!player || !player.dataset.videoEmbed) return;

  const iframe = document.createElement("iframe");
  iframe.src = `${player.dataset.videoEmbed}?autoplay=true&loop=false&muted=false&preload=true&responsive=true`;
  iframe.title = player.dataset.videoTitle || "Tau demo video";
  iframe.loading = "lazy";
  iframe.allow = "accelerometer; gyroscope; autoplay; encrypted-media; picture-in-picture";
  iframe.allowFullscreen = true;

  player.replaceChildren(iframe);
});
