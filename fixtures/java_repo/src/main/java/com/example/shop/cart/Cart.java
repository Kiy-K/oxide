package com.example.shop.cart;

import static com.example.shop.util.Strings.join;

import com.example.shop.store.*;
import com.example.shop.util.Strings;
import java.util.ArrayList;
import java.util.List;
import javax.swing.JPanel;

public class Cart {
    private final Store store;
    private final List<Key> keys = new ArrayList<>();

    public Cart(Store store) {
        this.store = store;
    }

    public void add(String name) {
        Key key = Key.of(name);
        keys.add(key);
        store.put(key, name);
    }

    public String describe() {
        return join("cart", Strings.join("items", names()));
    }

    String names() {
        return String.valueOf(keys.size());
    }

    void listen(Runnable later) {
        Runnable task = new Runnable() {
            @Override
            public void run() {
                describe();
            }
        };
        later.run();
    }

    /** A Swing view: its inherited members are unknown. */
    class View extends JPanel {
        void refresh() {
            describe();
            repaint();
        }
    }

    class Summary {
        String text() {
            return describe();
        }
    }
}
