"""模型注册 - import 所有模型以便 Base.metadata.create_all 能建表"""
from models.operator import Operator
from models.kol import Kol
from models.kol_email import KolEmail
from models.project_assessment import ProjectAssessment
from models.thread import Thread
from models.message import Message
from models.note import Note
from models.send_log import SendLog

__all__ = ["Operator", "Kol", "KolEmail", "ProjectAssessment", "Thread", "Message", "Note", "SendLog"]
