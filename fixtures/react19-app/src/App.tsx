import Sidebar from './components/Sidebar';
import CardList from './components/CardList';
import Form from './components/Form';
import { navItems, cards } from './data';

export default function App() {
  return (
    <div className="layout">
      <Sidebar items={navItems} />
      <main>
        <h1>Projects</h1>
        <CardList cards={cards} />
        <Form />
      </main>
    </div>
  );
}
