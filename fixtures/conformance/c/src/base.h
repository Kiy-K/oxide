#ifndef BASE_H
#define BASE_H

#define BASE_VERSION 1

struct base {
    int refcount;
};

int base_retain(struct base *b);

#endif
