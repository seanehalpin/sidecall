import { useState, type FormEvent } from 'react';

export default function Form() {
  const [name, setName] = useState('');
  const [role, setRole] = useState('viewer');
  const [submitted, setSubmitted] = useState('');

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitted(`${name} (${role})`);
  }

  return (
    <form className="invite-form" onSubmit={onSubmit}>
      <h2>Invite someone</h2>
      <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
      <select value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="viewer">Viewer</option>
        <option value="editor">Editor</option>
        <option value="admin">Admin</option>
      </select>
      <button type="submit" data-testid="submit">Send invite</button>
      {submitted && <p>Invited {submitted}</p>}
    </form>
  );
}
