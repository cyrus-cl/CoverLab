export function BrandLockup() {
  return (
    <div className="brand-lockup">
      <span className="brand-symbol" aria-hidden="true">
        <span>CL</span>
        <img src="/brand/coverlab-icon-rounded.png" alt="" width="48" height="48" onError={(event) => { event.currentTarget.hidden = true; }} />
      </span>
      <div><h1>CoverLab</h1><p>唱作实验室</p></div>
    </div>
  );
}
