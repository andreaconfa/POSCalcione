from sqlmodel import SQLModel, create_engine, Session, select
from .models import Kitchen, Product, Category
import os
from .models_customizations import ProductPrompt, OrderLineOption  
from .receipts.models_receipts import Printer, ReceiptTemplate, ReceiptRule, ReceiptRuleProduct

DB_URL = os.getenv("CALCIONE_DB_URL", "sqlite:///app.db")
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, echo=False, connect_args=connect_args)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    SQLModel.metadata.create_all(engine)

def get_session():
    return Session(engine)
def get_db():
    with Session(engine) as session:
        yield session
        
def seed_if_empty():
    with get_session() as session:
        if not session.exec(select(Kitchen)).first():
            casetta = Kitchen(name="Casetta", prefix="C", next_seq=1)
            esterno = Kitchen(name="Esterno", prefix="E", next_seq=1)
            session.add(casetta); session.add(esterno); session.commit()
        if not session.exec(select(Product)).first():
            casetta = session.exec(select(Kitchen).where(Kitchen.name=="Casetta")).one()
            esterno = session.exec(select(Kitchen).where(Kitchen.name=="Esterno")).one()
            session.add_all([
                Product(name="Panino porchetta", price_cents=700, kitchen_id=casetta.id),
                Product(name="Salsiccia", price_cents=800, kitchen_id=casetta.id),
                Product(name="Patatine", price_cents=400, kitchen_id=casetta.id),
                Product(name="Acqua 0.5L", price_cents=100, kitchen_id=esterno.id),
                Product(name="Birra 0.4L", price_cents=400, kitchen_id=esterno.id),
                Product(name="Bibita 0.33L", price_cents=250, kitchen_id=esterno.id),
            ])
            session.commit()
            
def seed_receipts_if_empty(session):
    # Stampante di default
    if not session.exec(select(Printer)).first():
        p = Printer(name="Stampante Principale", host="192.168.1.50", port=9100, enabled=True)
        session.add(p)
        session.commit()

    # Template base KDS
    if not session.exec(select(ReceiptTemplate)).first():
        t1 = ReceiptTemplate(
            name="KDS Base",
            body=(
                "{{ event_name }}\n"
                "{{ now.strftime('%d/%m/%Y %H:%M') }}\n"
                "------------------------------\n"
                "{% if kitchen %}BANCO: {{ kitchen.prefix }} - {{ kitchen.name }}\n"
                "Nr. Ritiro: {{ kitchen.pickup_seq }}\n"
                "------------------------------\n{% endif %}"
                "{% for l in lines %}{{ '%2dx ' % l.qty }}{{ l.name }}\n"
                "{% if l.options %}  ({{ ', '.join(l.options) }})\n{% endif %}"
                "{% if l.notes %}  NOTE: {{ l.notes }}\n{% endif %}"
                "{% endfor %}"
            ),
            cut=True
        )
        t2 = ReceiptTemplate(
            name="Immediato",
            body=(
                "{{ event_name }}\n"
                "{{ now.strftime('%d/%m/%Y %H:%M') }}\n"
                "------------------------------\n"
                "RITIRO IMMEDIATO\n"
                "------------------------------\n"
                "{% for l in lines %}{{ '%2dx ' % l.qty }}{{ l.name }}\n{% endfor %}"
            ),
            cut=True
        )
        session.add(t1); session.add(t2); session.commit()

    # Regole bootstrap equivalenti alla vecchia logica
    p = session.exec(select(Printer)).first()
    t_kds = session.exec(select(ReceiptTemplate).where(ReceiptTemplate.name=="KDS Base")).first()
    t_imm = session.exec(select(ReceiptTemplate).where(ReceiptTemplate.name=="Immediato")).first()

    if not session.exec(select(ReceiptRule)).first():
        # per ogni kitchen: regola KDS
        kitchens = session.exec(select(Kitchen)).all()
        pr = 10
        for k in kitchens:
            rr = ReceiptRule(
                name=f"KDS — {k.name}",
                mode="kds",
                kitchen_id=k.id,
                printer_id=p.id,
                template_id=t_kds.id,
                copies=1,
                priority=pr,
                consume_lines=True,
                enabled=True
            )
            pr += 10
            session.add(rr)
        session.commit()

        # regola fallback: prodotti senza kitchen -> Immediato
        no_kds = ReceiptRule(
            name="Non KDS — Immediato",
            mode="product_set",
            kitchen_id=None,
            printer_id=p.id,
            template_id=t_imm.id,
            copies=1,
            priority=pr,
            consume_lines=True,
            enabled=True
        )
        session.add(no_kds); session.commit()

        # associa i prodotti senza kitchen
        prods = session.exec(select(Product).where(Product.kitchen_id == None)).all()
        for prod in prods:
            session.add(ReceiptRuleProduct(rule_id=no_kds.id, product_id=prod.id))
        session.commit()