from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    student_id = Column(String, nullable=True)
    brs_username = Column(String, nullable=True)
    brs_password = Column(String, nullable=True)
    brs_cookies = Column(String, nullable=True)
    name = Column(String, nullable=True)
    course_number = Column(Integer, nullable=True)
    group_number = Column(Integer, nullable=True)
    subgroup_number = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    schedules = relationship('Schedule', back_populates='user', cascade='all, delete-orphan')
    grades = relationship('Grade', back_populates='user', cascade='all, delete-orphan')
    reminders = relationship('Reminder', back_populates='user', cascade='all, delete-orphan')

class Schedule(Base):
    __tablename__ = 'schedule'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    subject_name = Column(String, nullable=False)
    teacher_name = Column(String, nullable=True)
    date_time = Column(DateTime, nullable=False)
    location = Column(String, nullable=True)
    is_online = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User', back_populates='schedules')

class Grade(Base):
    __tablename__ = 'grades'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    subject_name = Column(String, nullable=False)
    current_grade = Column(Float, nullable=False)
    max_grade = Column(Float, default=100.0)
    attendance_percent = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User', back_populates='grades')

class Reminder(Base):
    __tablename__ = 'reminders'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    event_type = Column(String)
    event_name = Column(String, nullable=False)
    remind_at = Column(DateTime, nullable=False)
    is_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User', back_populates='reminders')

class ReminderSettings(Base):
    __tablename__ = 'reminder_settings'
    telegram_id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, default=False)
    minutes_before = Column(Integer, default=15)