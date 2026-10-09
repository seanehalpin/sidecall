import { useState } from 'react';

type Props = { title: string; body: string };

export default function Card({ title, body }: Props) {
  const [starred, setStarred] = useState(false);
  return (
    <article className="card">
      <header className="card-header">
        <h2 className="card-title" data-testid="card-title">{title}</h2>
      </header>
      <p className="card-body">{body}</p>
      <button type="button" onClick={() => setStarred((s) => !s)}>
        {starred ? 'Starred' : 'Star'}
      </button>
    </article>
  );
}
