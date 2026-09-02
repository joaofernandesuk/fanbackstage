import Link from "next/link";

export default function NotFound() {
  return (
    <section className="not-found-page card" aria-labelledby="not-found-title">
      <p className="eyebrow">PAGE NOT FOUND</p>
      <h1 id="not-found-title">We couldn’t find that page.</h1>
      <p>
        The link may be out of date, or this area may not be available in the web app.
      </p>
      <div className="not-found-actions">
        <Link className="button" href="/">Go to home</Link>
        <Link className="link" href="/discover">Discover creators</Link>
      </div>
    </section>
  );
}
