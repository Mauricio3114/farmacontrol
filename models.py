from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db, login_manager


def agora_brasil():
    try:
        return datetime.now(ZoneInfo("America/Fortaleza"))
    except ZoneInfoNotFoundError:
        return datetime.now()


class Farmacia(db.Model):
    __tablename__ = "farmacias"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cnpj = db.Column(db.String(30), unique=True, nullable=True)
    telefone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(150), nullable=True)
    endereco = db.Column(db.String(255), nullable=True)
    cidade = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="ativa")
    plano = db.Column(db.String(50), nullable=False, default="basico")
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    usuarios = db.relationship("User", backref="farmacia", lazy=True)
    clientes = db.relationship("Cliente", backref="farmacia", lazy=True)
    entregadores = db.relationship("Entregador", backref="farmacia", lazy=True)
    pedidos = db.relationship("Pedido", backref="farmacia", lazy=True)
    localizacoes = db.relationship("Localizacao", backref="farmacia", lazy=True)
    whatsapp_configs = db.relationship("WhatsAppConfig", backref="farmacia", lazy=True)
    whatsapp_logs = db.relationship("WhatsAppLog", backref="farmacia", lazy=True)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)

    # master = admin geral do SaaS
    # admin = admin da farmácia
    perfil = db.Column(db.String(20), nullable=False, default="admin")

    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=True)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    def set_password(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_password(self, senha):
        return check_password_hash(self.senha_hash, senha)

    @property
    def is_master(self):
        return self.perfil == "master"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Cliente(db.Model):
    __tablename__ = "clientes"

    id = db.Column(db.Integer, primary_key=True)
    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=False)

    nome = db.Column(db.String(150), nullable=False)
    telefone = db.Column(db.String(30), nullable=False)
    endereco = db.Column(db.String(255), nullable=False)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    pedidos = db.relationship("Pedido", backref="cliente", lazy=True)


class Entregador(db.Model):
    __tablename__ = "entregadores"

    id = db.Column(db.Integer, primary_key=True)
    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=False)

    nome = db.Column(db.String(150), nullable=False)
    telefone = db.Column(db.String(30), nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    pedidos = db.relationship("Pedido", backref="entregador", lazy=True)
    localizacoes = db.relationship("Localizacao", backref="entregador", lazy=True)

    __table_args__ = (
        db.UniqueConstraint("farmacia_id", "telefone", name="uq_entregador_telefone_farmacia"),
    )

    def set_password(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_password(self, senha):
        return check_password_hash(self.senha_hash, senha)


class Pedido(db.Model):
    __tablename__ = "pedidos"

    id = db.Column(db.Integer, primary_key=True)
    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=False)

    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    entregador_id = db.Column(db.Integer, db.ForeignKey("entregadores.id"), nullable=True)

    status = db.Column(db.String(30), nullable=False, default="recebido")
    codigo_rastreio = db.Column(db.String(40), unique=True, nullable=True)

    criado_em = db.Column(db.DateTime, default=agora_brasil)
    saiu_entrega_em = db.Column(db.DateTime, nullable=True)
    entregue_em = db.Column(db.DateTime, nullable=True)


class Localizacao(db.Model):
    __tablename__ = "localizacoes"

    id = db.Column(db.Integer, primary_key=True)
    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=False)
    entregador_id = db.Column(db.Integer, db.ForeignKey("entregadores.id"), nullable=False)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id"), nullable=True)

    latitude = db.Column(db.String(50), nullable=False)
    longitude = db.Column(db.String(50), nullable=False)
    data_hora = db.Column(db.DateTime, default=agora_brasil)


class WhatsAppConfig(db.Model):
    __tablename__ = "whatsapp_config"

    id = db.Column(db.Integer, primary_key=True)
    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=False, unique=True)

    ativo = db.Column(db.Boolean, default=False)

    access_token = db.Column(db.Text, nullable=True)
    phone_number_id = db.Column(db.String(100), nullable=True)
    business_account_id = db.Column(db.String(100), nullable=True)
    verify_token = db.Column(db.String(150), nullable=True)

    nome_template_pedido_recebido = db.Column(db.String(100), nullable=True)
    nome_template_saiu_entrega = db.Column(db.String(100), nullable=True)
    nome_template_pedido_entregue = db.Column(db.String(100), nullable=True)

    enviar_pedido_recebido = db.Column(db.Boolean, default=True)
    enviar_saiu_entrega = db.Column(db.Boolean, default=True)
    enviar_pedido_entregue = db.Column(db.Boolean, default=True)

    criado_em = db.Column(db.DateTime, default=agora_brasil)
    atualizado_em = db.Column(db.DateTime, default=agora_brasil, onupdate=agora_brasil)


class WhatsAppLog(db.Model):
    __tablename__ = "whatsapp_logs"

    id = db.Column(db.Integer, primary_key=True)
    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=True)

    tipo = db.Column(db.String(50), nullable=False)
    destino = db.Column(db.String(30), nullable=True)
    mensagem = db.Column(db.Text, nullable=True)
    template_nome = db.Column(db.String(100), nullable=True)

    pedido_id = db.Column(db.Integer, db.ForeignKey("pedidos.id"), nullable=True)

    status = db.Column(db.String(50), nullable=False, default="pendente")
    resposta_api = db.Column(db.Text, nullable=True)
    direction = db.Column(db.String(20), nullable=False, default="outbound")

    criado_em = db.Column(db.DateTime, default=agora_brasil)