import { useState } from 'react';

export default function Sidebar({ items }: { items: string[] }) {
  const [active, setActive] = useState('Inbox');
  return (
    <nav className="sidebar">
      <ul>
        {items.map((item) => (
          <li key={item}>
            <a
              href={`#${item.toLowerCase()}`}
              className={active === item ? 'active' : undefined}
              data-testid="nav-item"
              onClick={() => setActive(item)}
            >
              {item}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
