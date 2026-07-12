from app.routes.admin import AdminUserController
from app.routes.auth import AuthController, ProfileController
from app.routes.banking import BankingController
from app.routes.categories import CategoryController
from app.routes.csv import CsvController
from app.routes.expenses import ExpenseController
from app.routes.frontend import FrontendController
from app.routes.reports import ReportController
from app.routes.trackers import TrackerController
from app.routes.users import UserController


route_handlers = [
    FrontendController,
    AuthController,
    ProfileController,
    UserController,
    AdminUserController,
    TrackerController,
    BankingController,
    CategoryController,
    ExpenseController,
    ReportController,
    CsvController,
]
