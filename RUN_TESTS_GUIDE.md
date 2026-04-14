# 🧪 Complete Guide to Running Tests

## Quick Start (3 Steps)

### Step 1: Install Dependencies
```bash
cd /home/tarek/Desktop/PPW
pip install -r requirements/dev.txt
```

**What this installs:**
- pytest (test runner)
- pytest-django (Django integration)
- pytest-cov (coverage reporting)
- factory-boy (test data factories)
- pytest-mock (mocking)
- faker (fake data)
- pytest-freezegun (time freezing)

**Expected output:** `Successfully installed ...` with package list

---

### Step 2: Run All Tests
```bash
pytest
```

**Expected output:**
```
==================== test session starts ====================
platform linux -- Python 3.x.x, pytest-x.x.x, ...
django: version x.x.x, settings 'ppw.settings.test'
collected 90+ items

tests/authentication/test_login.py ..........                    [ 11%]
tests/authentication/test_profile_switching.py ......            [ 17%]
tests/authentication/test_registration.py ........               [ 25%]
tests/authentication/test_user_model.py ............              [ 39%]
tests/auctions/test_auction_business_validator.py ...........    [ 51%]
tests/auctions/test_auction_lifecycle.py ............              [ 65%]
tests/auctions/test_bid_business_validator.py ..............      [ 81%]
tests/auctions/test_edge_cases.py ................                [100%]

==================== 90+ passed in 5.23s ====================
```

---

### Step 3: View Coverage Report
```bash
pytest --cov=apps --cov-report=html
```

Then open the report:
```bash
open htmlcov/index.html  # macOS
# OR
xdg-open htmlcov/index.html  # Linux
```

---

## Command Reference

### Run All Tests
```bash
pytest
```
Simple, no frills. Just show pass/fail.

### Run Tests (Verbose)
Shows each test name and result:
```bash
pytest -v
```

**Output example:**
```
tests/authentication/test_login.py::test_login_valid_credentials PASSED
tests/authentication/test_login.py::test_login_invalid_password PASSED
...
```

### Run Specific Test File
```bash
# Authentication tests
pytest tests/authentication/

# Auction tests
pytest tests/auctions/

# Single file
pytest tests/authentication/test_login.py
```

### Run Specific Test Function
```bash
# Run one test
pytest tests/authentication/test_login.py::test_login_valid_credentials

# Run tests matching pattern
pytest -k "login"  # runs all tests with "login" in name
pytest -k "valid"  # runs all tests with "valid" in name
```

### Coverage Reports

#### Terminal Report (Quick)
```bash
pytest --cov=apps
```

Shows coverage percentage in terminal:
```
Name                           Stmts   Miss  Cover
--------------------------------------------------
apps/authentication/models.py     50      5    90%
apps/authentication/views.py      80     10    87%
apps/auctions/models.py          120     15    87%
apps/auctions/services.py        200     30    85%
--------------------------------------------------
TOTAL                            450     60    87%
```

#### HTML Report (Detailed)
```bash
pytest --cov=apps --cov-report=html
```

Creates `htmlcov/index.html` with:
- Line-by-line coverage
- Color-coded (green=covered, red=uncovered)
- Drill-down to see missing lines

#### With Report Threshold
```bash
pytest --cov=apps --cov-fail-under=80
```

Fails if coverage drops below 80%

---

## Common Workflows

### Run Tests After Making Changes
```bash
pytest -v
```

### Check Authentication Module Coverage
```bash
pytest tests/authentication/ --cov=apps.authentication --cov-report=term-missing
```

### Run Tests in Parallel (Fast!)
```bash
pytest -n auto
```
Requires: `pip install pytest-xdist`

### Debug a Failing Test
```bash
# Verbose output
pytest tests/path/to/test.py::test_name -v

# Show print statements
pytest tests/path/to/test.py::test_name -v -s

# Drop into debugger on failure
pytest tests/path/to/test.py::test_name --pdb
```

### Run Only Failed Tests from Last Run
```bash
pytest --lf
```

### Stop on First Failure
```bash
pytest -x
```

### Run Last 5 Failed Tests
```bash
pytest --lf -x
```

---

## Environment-Specific Settings

### Test Database Configuration
Already set in `ppw/settings/test.py`:
- **Database**: SQLite in-memory (`:memory:`)
- **Speed**: 10-30x faster than file-based DB
- **Isolation**: Each test starts fresh

### Django Settings
Tests automatically use:
```
DJANGO_SETTINGS_MODULE=ppw.settings.test
```

Configured in `pytest.ini`

---

## Troubleshooting

### "pytest: command not found"
**Solution:** Install dependencies
```bash
pip install -r requirements/dev.txt
```

### "ModuleNotFoundError: No module named 'ppw'"
**Solution:** You're in wrong directory
```bash
cd /home/tarek/Desktop/PPW
pytest
```

### "django.core.exceptions.ImproperlyConfigured"
**Solution:** Settings not found - check Django setup
```bash
# Verify test settings exist
ls -la ppw/settings/test.py

# Check pytest.ini
cat pytest.ini
```

### Tests are Slow
**Solution 1:** Run in parallel
```bash
pip install pytest-xdist
pytest -n auto
```

**Solution 2:** Run specific test file
```bash
pytest tests/authentication/test_login.py  # faster than all
```

### Database Lock Issues
**Solution:** SQLite in-memory is isolated per test
```bash
# If you get locks, restart pytest
pytest --cache-clear
```

---

## Test Organization Reference

```
tests/
├── conftest.py                          # Shared fixtures
├── factories.py                         # Test data factories
│
├── authentication/                      # 36 tests
│   ├── test_registration.py            # 8 tests
│   ├── test_login.py                   # 10 tests
│   ├── test_profile_switching.py       # 6 tests
│   └── test_user_model.py              # 12 tests
│
└── auctions/                            # 52+ tests
    ├── test_bid_business_validator.py  # 14 tests
    ├── test_auction_business_validator.py # 11 tests
    ├── test_auction_lifecycle.py       # 12 tests
    └── test_edge_cases.py              # 15+ tests
```

---

## Coverage Goals

| Component | Target | Current |
|-----------|--------|---------|
| Authentication | 90%+ | ✓ Ready |
| Registration | 95%+ | ✓ Ready |
| Login | 95%+ | ✓ Ready |
| Profiles | 90%+ | ✓ Ready |
| User Model | 90%+ | ✓ Ready |
| Auctions | 85%+ | ✓ Ready |
| Bid Validator | 95%+ | ✓ Ready |
| Auction Validator | 95%+ | ✓ Ready |
| Lifecycle | 80%+ | ✓ Ready |
| Edge Cases | 85%+ | ✓ Ready |

---

## Real-World Examples

### Example 1: Develop Feature + Run Tests
```bash
# Make changes to code
nano apps/authentication/views.py

# Run related tests
pytest tests/authentication/test_login.py -v

# Check coverage
pytest tests/authentication/ --cov=apps.authentication
```

### Example 2: Debug Failing Test
```bash
# See what failed
pytest tests/auctions/test_bid_business_validator.py -v

# Run with more details
pytest tests/auctions/test_bid_business_validator.py::test_seller_cannot_bid_on_own_auction -v -s

# Use debugger
pytest tests/auctions/test_bid_business_validator.py::test_seller_cannot_bid_on_own_auction --pdb
```

### Example 3: Pre-Commit Check
```bash
# Run all tests with coverage
pytest --cov=apps --cov-report=term-missing --cov-fail-under=80

# If coverage drops below 80%, this fails
```

### Example 4: Run Interactive Menu
```bash
./run_tests.sh
```

Provides menu:
```
Select an option:
1) Run all tests
2) Run tests with coverage report
3) Run authentication tests only
4) Run auction tests only
5) Run validators tests only
6) Run with verbose output
7) Run tests in parallel (fast)
8) Show coverage report
```

---

## Continuous Integration Setup

For CI/CD pipelines (GitHub Actions, GitLab CI, etc.):

```bash
# Install
pip install -r requirements/dev.txt

# Run tests with coverage
pytest --cov=apps --cov-report=xml --cov-fail-under=80

# Report results
# Coverage XML is used by CI systems for reporting
```

---

## Next Steps

1. ✅ **Install**: `pip install -r requirements/dev.txt`
2. ✅ **Run**: `pytest`
3. ✅ **View Coverage**: `pytest --cov=apps --cov-report=html`
4. 📖 **Read Tests**: Open `tests/README.md` for detailed examples
5. ✍️ **Write Tests**: Follow patterns in existing test files

---

## Quick Reference Card

```
INSTALL:           pip install -r requirements/dev.txt
RUN ALL:           pytest
RUN VERBOSE:       pytest -v
RUN WITH COVERAGE: pytest --cov=apps
VIEW HTML REPORT:  pytest --cov=apps --cov-report=html && open htmlcov/index.html
RUN SPECIFIC:      pytest tests/authentication/test_login.py
FAST (PARALLEL):   pytest -n auto
DEBUG:             pytest tests/path/test.py::test_name -v -s --pdb
PATTERN:           pytest -k "pattern_name"
```

---

**You're all set! Start with `pytest` and watch your tests pass! 🎉**
