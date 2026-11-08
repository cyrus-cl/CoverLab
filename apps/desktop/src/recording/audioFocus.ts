/** A single audible player across full-track audition, selected audition and takes. */
export function claimAudioFocus(active: HTMLMediaElement | null): void {
  window.dispatchEvent(new CustomEvent("coverlab:audio-focus", { detail: { active } }));
  document.querySelectorAll<HTMLMediaElement>("audio,video").forEach(media => {
    if (media !== active && !media.paused) media.pause();
  });
}
