from flask import Flask
from flask_login import login_user, LoginManager, UserMixin, current_user,login_manager
from collections import namedtuple
import os
# from flask_sqlalchemy import SQLAlchemy
# from sqlalchemy.ext.associationproxy import association_proxy
# from sqlalchemy.orm import relationship, backref
# import flask_admin as admin
# from flask_admin.contrib import sqla
makeuser = namedtuple('User',['password','role','org'])

user_db = {
    'admin' : makeuser('admin','admin','A'),
    'user1' : makeuser('user1','user','A'),
    'user2' : makeuser('user2','user','B')
}

class server_config:
    SECRET_KEY = os.urandom(24)
    SQLALCHEMY_DATABASE_URI= 'sqlite:///test.db'
    SQLALCHEMY_ECHO = True

server = Flask(__name__)
server.config.from_object(server_config)

class User(UserMixin):
    def __init__(self, username):
        self.id = username
        self.role = user_db.get(username).role
        self.org = user_db.get(username).org

login_manager = LoginManager()
login_manager.init_app(server)
login_manager.login_view = "/login"

@login_manager.user_loader
def load_user(username):
    return User(username)

# db = SQLAlchemy()
# db.init_app(server)

# class Users(db.Model):
#     __tablename__ = 'user'
#     id = db.Column(db.Integer, primary_key=True)
#     name = db.Column(db.String(64))

#     # Association proxy of "user_keywords" collection to "keyword" attribute - a list of keywords objects.
#     keywords = association_proxy('user_keywords', 'keyword')
#     # Association proxy to association proxy - a list of keywords strings.
#     keywords_values = association_proxy('user_keywords', 'keyword_value')

#     def __init__(self, name=None):
#         self.name = name


# class UserKeyword(db.Model):
#     __tablename__ = 'user_keyword'
#     user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
#     keyword_id = db.Column(db.Integer, db.ForeignKey('keyword.id'), primary_key=True)
#     special_key = db.Column(db.String(50))

#     # bidirectional attribute/collection of "user"/"user_keywords"
#     user = relationship(Users, backref=backref("user_keywords", cascade="all, delete-orphan"))

#     # reference to the "Keyword" object
#     keyword = relationship("Keyword")
#     # Reference to the "keyword" column inside the "Keyword" object.
#     keyword_value = association_proxy('keyword', 'keyword')

#     def __init__(self, keyword=None, user=None, special_key=None):
#         self.user = user
#         self.keyword = keyword
#         self.special_key = special_key


# class Keyword(db.Model):
#     __tablename__ = 'keyword'
#     id = db.Column(db.Integer, primary_key=True)
#     keyword = db.Column('keyword', db.String(64))

#     def __init__(self, keyword=None):
#         self.keyword = keyword

#     def __repr__(self):
#         return 'Keyword(%s)' % repr(self.keyword)


# class UserAdmin(sqla.ModelView):
#     """ Flask-admin can not automatically find a association_proxy yet. You will
#         need to manually define the column in list_view/filters/sorting/etc.
#         Moreover, support for association proxies to association proxies
#         (e.g.: keywords_values) is currently limited to column_list only."""

#     column_list = ('id', 'name', 'keywords', 'keywords_values')
#     column_sortable_list = ('id', 'name')
#     column_filters = ('id', 'name', 'keywords')
#     form_columns = ('name', 'keywords')


# class KeywordAdmin(sqla.ModelView):
#     column_list = ('id', 'keyword')


# Create admin
# admin = admin.Admin(server, name='Example: SQLAlchemy Association Proxy', template_mode='bootstrap4')
# admin.add_view(UserAdmin(Users, db.session))
# admin.add_view(KeywordAdmin(Keyword, db.session))

# if __name__ == '__main__':

    # Create DB
# with server.app_context():
    # db.create_all()

    # Add sample data
# user = Users('log')
# for kw in (Keyword('new_from_blammo'), Keyword('its_big')):
#     user.keywords.append(kw)

# with server.app_context():
#     db.session.add(user)
#     db.session.commit()

    # # Start app
    # app.run(debug=True)