from django.contrib.auth.models import AbstractUser


class UserAccount(AbstractUser):
    USERNAME_FIELD = "username"




class Manager(UserAccount):
    pass


class Sellar(UserAccount):
    pass

class Customer(UserAccount):
    pass