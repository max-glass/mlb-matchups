from __future__ import annotations

from pathlib import Path

from sqlalchemy import Column, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.paths import DATA_DIR

Base = declarative_base()


class AnalysisRow(Base):
    __tablename__ = "analyses"
    id = Column(String, primary_key=True)
    created_at = Column(String, nullable=False)
    model_version = Column(String, nullable=False)
    game_date = Column(String, nullable=False)
    game_pk = Column(Integer)
    payload_json = Column(Text, nullable=False)
    sim_path = Column(String, nullable=False)


class OddsRow(Base):
    __tablename__ = "odds_snapshots"
    id = Column(Integer, primary_key=True)
    created_at = Column(String, nullable=False)
    sportsbook = Column(String, nullable=False)
    source = Column(String, nullable=False)
    quoted_at = Column(String, nullable=False)
    game_date = Column(String, nullable=False)
    game_pk = Column(Integer)
    away_team_id = Column(Integer)
    home_team_id = Column(Integer)
    market_type = Column(String, nullable=False)
    player_id = Column(Integer)
    team_side = Column(String)
    selection = Column(String, nullable=False)
    line = Column(String)
    american = Column(Integer, nullable=False)
    other_side_american = Column(Integer)
    context = Column(String, nullable=False, default="pregame")


class CardRow(Base):
    __tablename__ = "cards"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=0)
    document_json = Column(Text, nullable=False)


class CardVersionRow(Base):
    __tablename__ = "card_versions"
    id = Column(Integer, primary_key=True)
    card_id = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    created_at = Column(String, nullable=False)
    snapshot_json = Column(Text, nullable=False)


class SettingsRow(Base):
    __tablename__ = "user_settings"
    id = Column(Integer, primary_key=True)
    document_json = Column(Text, nullable=False)


def make_engine(database_url: str | None = None):
    url = database_url or f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"
    connect = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


DATA_DIR.mkdir(parents=True, exist_ok=True)
