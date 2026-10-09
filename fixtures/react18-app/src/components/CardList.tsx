import Card from './Card';
import type { CardData } from '../data';

export default function CardList({ cards }: { cards: CardData[] }) {
  return (
    <section className="card-list">
      {cards.map((card) => (
        <Card key={card.id} title={card.title} body={card.body} />
      ))}
    </section>
  );
}
