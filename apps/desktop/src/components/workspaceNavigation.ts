export type WorkflowStep = "import" | "prepare" | "lyrics" | "record";

function visibleWorkspaceControl(anchor:string):HTMLElement|null {
  for(const element of document.querySelectorAll<HTMLElement>(`[data-guide="${anchor}"]`)) {
    if(element.matches(":disabled,[aria-disabled='true']"))continue;
    let visible=true;
    for(let node:HTMLElement|null=element;node;node=node.parentElement){
      const style=window.getComputedStyle(node);
      if(node.hidden||node.hasAttribute("inert")||node.getAttribute("aria-hidden")==="true"||node.getAttribute("aria-disabled")==="true"||style.display==="none"||style.visibility==="hidden")visible=false;
      if(node.tagName==="DETAILS"&&!(node as HTMLDetailsElement).open&&!node.querySelector("summary")?.contains(element))visible=false;
    }
    if(visible)return element;
  }
  return null;
}

/** Navigation only: never activates processing, playback or recording. */
export function focusWorkflowStep(step: WorkflowStep): boolean {
  let target: HTMLElement | null = null;
  if (step === "record") {
    const recording = visibleWorkspaceControl("record");
    target = recording ? [...recording.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")].find(button=>button.textContent?.trim()==="开始录制")??recording.querySelector<HTMLButtonElement>(".recording-panel__actions button:not(:disabled)")??recording : null;
  } else {
    target = visibleWorkspaceControl(step);
  }
  target ??= document.querySelector<HTMLElement>(`[data-workflow="${step}"]`);
  if (!target) return false;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView?.({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
  target.focus({ preventScroll: true });
  return true;
}

export function focusUnsavedContent(): void {
  const saveNames=["保存歌词与时间","导入并保存","保存到项目","重试保存"];
  const buttons=document.querySelectorAll<HTMLButtonElement>(".lyrics-workspace button, .recording-panel button");
  for(const button of buttons) {
    if(button.disabled||!saveNames.includes(button.textContent?.trim()??""))continue;
    let visible=true;
    for(let node:HTMLElement|null=button;node;node=node.parentElement){
      const style=window.getComputedStyle(node);
      if(node.hidden||style.display==="none"||style.visibility==="hidden"||(node.tagName==="DETAILS"&&!(node as HTMLDetailsElement).open))visible=false;
    }
    if(!visible)continue;
    button.scrollIntoView?.({behavior:"auto",block:"center"});button.focus({preventScroll:true});return;
  }
  if(!focusWorkflowStep("lyrics"))focusWorkflowStep("record");
}
