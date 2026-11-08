import { afterEach, expect, it, vi } from "vitest";
import { claimAudioFocus } from "./audioFocus";
afterEach(()=>document.body.replaceChildren());
it("pauses the previous audible player and preserves the newly selected player",()=>{
  const previous=document.createElement("audio"),active=document.createElement("video");
  Object.defineProperty(previous,"paused",{value:false});Object.defineProperty(active,"paused",{value:false});
  previous.pause=vi.fn();active.pause=vi.fn();document.body.append(previous,active);
  claimAudioFocus(active);
  expect(previous.pause).toHaveBeenCalledOnce();expect(active.pause).not.toHaveBeenCalled();
  claimAudioFocus(null);expect(active.pause).toHaveBeenCalledOnce();
});
