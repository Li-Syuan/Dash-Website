from flask import Flask, request, redirect, session
from flask_login import login_user, LoginManager, UserMixin, current_user,login_manager,login_required
from collections import namedtuple
import os

makeuser = namedtuple('User',['password','role'])

user_db = {
    'admin' : makeuser('admin','admin'),
    'user1' : makeuser('test1','user'),
    'user2' : makeuser('test2','user')
}

class server_config:
    SECRET_KEY = os.urandom(24)
    
server = Flask(__name__)
server.config.from_object(server_config)

class User(UserMixin):
    def __init__(self, username):
        self.id = username
        self.role = user_db.get(username).role

login_manager = LoginManager()
login_manager.init_app(server)
login_manager.login_view = "/login"

@login_manager.user_loader
def load_user(username):
    return User(username)

