import ShieldCheck from "lucide-react/icons/shield-check";

export function PrivacyBadge() {
  return (
    <div className="privacy-badge" role="status">
      <ShieldCheck aria-hidden="true" />
      <span>本机处理 · 文件不会上传</span>
    </div>
  );
}
