from rf_acceptance_math import percent

def discount(price, rate):
    return price + percent(price, rate)
