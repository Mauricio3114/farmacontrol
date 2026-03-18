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


class UsuarioFarmacia(db.Model):
    __tablename__ = "usuarios_farmacias"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=False)

    perfil = db.Column(db.String(20), nullable=False, default="admin")
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    usuario = db.relationship("User", back_populates="vinculos_farmacias")
    farmacia = db.relationship("Farmacia", back_populates="usuarios_vinculados")

    __table_args__ = (
        db.UniqueConstraint("usuario_id", "farmacia_id", name="uq_usuario_farmacia"),
    )


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

    usuarios_vinculados = db.relationship(
        "UsuarioFarmacia",
        back_populates="farmacia",
        cascade="all, delete-orphan",
        lazy=True,
    )

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

    perfil = db.Column(db.String(20), nullable=False, default="admin")

    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=True)

    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    vinculos_farmacias = db.relationship(
        "UsuarioFarmacia",
        back_populates="usuario",
        cascade="all, delete-orphan",
        lazy=True,
    )

    def set_password(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_password(self, senha):
        return check_password_hash(self.senha_hash, senha)

    @property
    def is_master(self):
        return self.perfil == "master"

    @property
    def farmacias_ids(self):
        ids = [
            vinculo.farmacia_id
            for vinculo in self.vinculos_farmacias
            if vinculo.ativo
        ]

        if not ids and self.farmacia_id:
            ids = [self.farmacia_id]

        return ids

    @property
    def farmacia_principal_id(self):
        if self.farmacia_id:
            return self.farmacia_id

        if self.farmacias_ids:
            return self.farmacias_ids[0]

        return None

    def possui_acesso_farmacia(self, farmacia_id):
        if self.is_master:
            return True
        return farmacia_id in self.farmacias_ids


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
    bairro = db.Column(db.String(100), nullable=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    pedidos = db.relationship("Pedido", backref="cliente", lazy=True)


class EntregadorFarmacia(db.Model):
    __tablename__ = "entregadores_farmacias"

    id = db.Column(db.Integer, primary_key=True)
    entregador_id = db.Column(db.Integer, db.ForeignKey("entregadores.id"), nullable=False)
    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    entregador = db.relationship("Entregador", back_populates="vinculos_farmacias")
    farmacia = db.relationship("Farmacia")

    __table_args__ = (
        db.UniqueConstraint("entregador_id", "farmacia_id", name="uq_entregador_farmacia"),
    )


class Entregador(db.Model):
    __tablename__ = "entregadores"

    id = db.Column(db.Integer, primary_key=True)

    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=True)

    nome = db.Column(db.String(150), nullable=False)
    telefone = db.Column(db.String(30), nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=agora_brasil)

    pedidos = db.relationship("Pedido", backref="entregador", lazy=True)
    localizacoes = db.relationship("Localizacao", backref="entregador", lazy=True)

    vinculos_farmacias = db.relationship(
        "EntregadorFarmacia",
        back_populates="entregador",
        cascade="all, delete-orphan",
        lazy=True
    )

    __table_args__ = (
        db.UniqueConstraint("telefone", name="uq_entregador_telefone_global"),
    )

    def set_password(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_password(self, senha):
        return check_password_hash(self.senha_hash, senha)

    @property
    def farmacias_ids(self):
        ids = [
            vinculo.farmacia_id
            for vinculo in self.vinculos_farmacias
            if vinculo.ativo
        ]

        if not ids and self.farmacia_id:
            ids = [self.farmacia_id]

        return ids

    def possui_acesso_farmacia(self, farmacia_id):
        return farmacia_id in self.farmacias_ids


class Pedido(db.Model):
    __tablename__ = "pedidos"

    id = db.Column(db.Integer, primary_key=True)
    farmacia_id = db.Column(db.Integer, db.ForeignKey("farmacias.id"), nullable=False)

    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    entregador_id = db.Column(db.Integer, db.ForeignKey("entregadores.id"), nullable=True)

    status = db.Column(db.String(30), nullable=False, default="recebido")
    codigo_rastreio = db.Column(db.String(40), unique=True, nullable=True)

    # 🔥 NOVO CAMPO PRO GRÁFICO
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    # MANTIDO (compatibilidade)
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