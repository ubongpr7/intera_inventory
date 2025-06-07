import random
import string

def generate_batch_code():
    """
    Generate a random batch code.

    The generated batch code consists of 8 characters, including
    a mix of uppercase letters and digits.

    Returns:
        str: The generated batch code.
    """
    length = 8
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))
