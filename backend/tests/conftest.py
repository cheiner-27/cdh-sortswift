import os
import tempfile

os.environ["SORTSWIFT_DATA_DIR"] = tempfile.mkdtemp(prefix="sortswift-test-")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CatalogCard, PriceData


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")  # fresh in-memory DB per test
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def card(db):
    c = CatalogCard(
        game="mtg", external_id="test-uuid-1", tcgplayer_product_id=111,
        set_code="MH3", set_name="Modern Horizons 3", collector_number="42",
        name="Test Bolt", rarity="rare", finishes=["normal", "foil"],
        languages=["en"], image_url="http://example.com/bolt.jpg",
    )
    db.add(c)
    db.add(PriceData(tcgplayer_product_id=111, sub_type="Normal",
                     market=10.0, mid=11.0, low=8.0, direct_low=9.0))
    db.add(PriceData(tcgplayer_product_id=111, sub_type="Foil",
                     market=25.0, mid=27.0, low=20.0, direct_low=None))
    db.commit()
    return c
