import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { PublicFooter, PublicHeader } from "../components/public";
import { Empty, Icon } from "../components/ui";
import { setMeta } from "../meta";

/** A real not-found page (the static server also answers these paths with HTTP 404). */
export default function NotFound({ signedIn }: { signedIn?: boolean }) {
  const loc = useLocation();
  useEffect(() => { setMeta({ title: "Page not found · Helm Terminals", index: false }); }, []);
  const body = (
    <div className="not-found">
      <Empty icon="search" title="Page not found" text={<>Nothing lives at <code className="mono">{loc.pathname}</code>. It may have moved, or the link may be mistyped.</>}
        action={<div className="row" style={{ justifyContent: "center" }}>
          <Link to="/" className="btn"><Icon name="arrowleft" size={14} />{signedIn ? "Back to dashboard" : "Back to Helm"}</Link>
          {signedIn ? <Link to="/launches" className="btn secondary">Launches</Link> : <Link to="/login?mode=login" className="btn secondary">Sign in</Link>}
        </div>} />
    </div>
  );
  if (signedIn) return <div className="card">{body}</div>;
  return <div className="pub"><PublicHeader /><main id="pub-main" className="pub-wrap">{body}</main><PublicFooter /></div>;
}
