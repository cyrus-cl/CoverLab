import { useEffect, useId, useRef } from "react";

export function UnsavedProjectDialog({onContinue,onSave,onDiscard}:{onContinue:()=>void;onSave:()=>void;onDiscard:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const titleId=useId();
  const descriptionId=useId();
  useEffect(()=>{
    const node=dialog.current;
    if(!node)return;
    const previous=document.activeElement as HTMLElement|null;
    if(typeof node.showModal==="function")node.showModal();
    else node.setAttribute("open","");
    node.querySelector<HTMLButtonElement>("button")?.focus();
    return ()=>{if(typeof node.close==="function"&&node.open)node.close();previous?.focus({preventScroll:true});};
  },[]);
  return <dialog ref={dialog} className="project-switch-dialog" aria-labelledby={titleId} aria-describedby={descriptionId} aria-modal="true" onCancel={event=>{event.preventDefault();onContinue();}} onKeyDown={event=>{
    if(event.key==="Escape"){event.preventDefault();onContinue();}
    if(event.key==="Tab"){
      const buttons=[...event.currentTarget.querySelectorAll<HTMLButtonElement>("button")];
      if(event.shiftKey&&document.activeElement===buttons[0]){event.preventDefault();buttons.at(-1)?.focus();}
      else if(!event.shiftKey&&document.activeElement===buttons.at(-1)){event.preventDefault();buttons[0]?.focus();}
    }
  }}>
    <h2 id={titleId}>当前内容尚未保存</h2><p id={descriptionId}>先保存歌词或录制，再切换项目。选择“去保存”会返回当前内容，保存后请再次选择目标项目。</p>
    <div className="project-switch-dialog__actions"><button type="button" className="button button--secondary" onClick={onContinue}>继续编辑</button><button type="button" className="button button--primary" onClick={onSave}>去保存</button><button type="button" className="button button--quiet button--danger" onClick={onDiscard}>放弃并切换</button></div>
  </dialog>;
}
